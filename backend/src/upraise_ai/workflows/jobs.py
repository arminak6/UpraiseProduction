import logging

from fastapi import HTTPException
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse

from upraise_ai.core.errors import IngestionError

log = logging.getLogger("upraise.jobs")


class JobService:
    """Fence cancellation, purge temporary output and durably acknowledge receipt."""

    def __init__(self, jobs, vectors, audit, staging):
        self.jobs, self.vectors, self.audit, self.staging = jobs, vectors, audit, staging

    def dispose(self, job_id, reason, principal):
        with self.jobs.job_transaction(job_id) as (conn, row):
            if (not row or row["deletion_requested_at"] is not None
                    or row["client_id"] != principal.client_id
                    or not principal.can_access_course(row["metadata"]["course_id"])):
                raise HTTPException(404, "JOB_NOT_FOUND")
            if row["cleanup_reason"]:
                if row["cleanup_reason"] != reason:
                    raise IngestionError("JOB_ACTION_CONFLICT", status=409)
                if row["cleanup_completed_at"]:
                    return
            else:
                allowed = ("QUEUED", "RUNNING") if reason == "CANCEL" else ("DONE", "FAILED")
                if row["status"] not in allowed:
                    raise IngestionError("JOB_ACTION_NOT_ALLOWED", status=409)
                self.jobs.request_cleanup(conn, job_id, reason)
        # The durable cancellation commits before touching files or vector storage.
        self.complete_cleanup(job_id)

    def complete_cleanup(self, job_id):
        row = self.jobs.get(job_id)
        if not row or not row["cleanup_requested_at"] or row["cleanup_completed_at"]:
            return
        try:
            # Lock artifacts before the DB row, matching the worker's lock order.
            with self.staging.artifact_lock(str(job_id)):
                with self.jobs.job_transaction(job_id) as (conn, row):
                    if row["cleanup_completed_at"]:
                        return
                    try:
                        if row["cleanup_reason"] == "CANCEL":
                            self.vectors.remove_document(row["document_id"], row["metadata"]["course_id"])
                        else:
                            self.vectors.purge_inactive_attempts(
                                row["document_id"], row["metadata"]["course_id"], row["active_attempt"]
                            )
                    except (ResponseHandlingException, UnexpectedResponse):
                        raise IngestionError("VECTOR_STORAGE_UNAVAILABLE", status=503) from None
                    self.staging.purge_output(str(job_id))
                    event = "JOB_CANCELLED" if row["cleanup_reason"] == "CANCEL" else "JOB_RECEIVED"
                    self.audit.record(row, event, {"reason": row["cleanup_reason"]})
                    self.jobs.finish_cleanup(conn, job_id)
        except OSError:
            raise IngestionError("TEMPORARY_OUTPUT_STORAGE_UNAVAILABLE", status=503) from None

    def recover_cleanup(self):
        # One busy worker must not starve unrelated acknowledgments/cancellations.
        for row in self.jobs.pending_cleanup():
            try:
                self.complete_cleanup(row["job_id"])
            except Exception:
                log.warning("Job %s cleanup pending; recovery will retry", row["job_id"])
