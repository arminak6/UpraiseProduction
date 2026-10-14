import logging
import signal
import threading
from pathlib import Path

import psycopg
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError, PartialCredentialsError
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse

from upraise_ai.core.config import get_settings
from upraise_ai.core.errors import IngestionError, LeaseLost
from upraise_ai.guards.operations import control_state, require_ai
from upraise_ai.schemas.documents import IngestionMetadata, QualityStatus
from upraise_ai.services.formats import get_processor
from upraise_ai.services.versions import VersionCatalog
from upraise_ai.storage.audit import AuditRepository
from upraise_ai.storage.files import StagingStore
from upraise_ai.storage.jobs import JobRepository
from upraise_ai.storage.lessons import LessonRepository
from upraise_ai.storage.vectors import VectorStore
from upraise_ai.workers.supervision import ChildTerminationError, JobSupervisor, run_recovery_child
from upraise_ai.workflows.documents import DocumentService
from upraise_ai.workflows.jobs import JobService
from upraise_ai.workflows.lessons import LessonService

log = logging.getLogger("upraise.worker")


def classify_error(error):
    if isinstance(error, IngestionError):
        return error.code, error.retryable
    if isinstance(error, (NoCredentialsError, PartialCredentialsError)):
        return "AWS_CREDENTIALS_UNAVAILABLE", False
    if isinstance(error, ClientError):
        code = error.response.get("Error", {}).get("Code", "")
        if code in (
            "ThrottlingException",
            "ServiceUnavailableException",
            "InternalServerException",
            "ModelTimeoutException",
        ):
            return "AWS_TEMPORARY_FAILURE", True
        return "AWS_REQUEST_REJECTED", False
    if isinstance(
        error, (psycopg.OperationalError, psycopg.InterfaceError, BotoCoreError, ResponseHandlingException)
    ):
        return "DEPENDENCY_UNAVAILABLE", True
    if isinstance(error, UnexpectedResponse):
        return "VECTOR_SERVICE_ERROR", error.status_code >= 500 or error.status_code == 429
    if isinstance(error, MemoryError):
        return "WORKER_MEMORY_EXHAUSTED", False
    return "PROCESSING_FAILED", False


class IngestionWorker:
    def __init__(self, settings, jobs=None, audit=None, vectors=None, processor=None, *, notify=None):
        self.settings = settings
        self.jobs = jobs or JobRepository(settings.jobs_dsn.get_secret_value())
        self.audit = audit or AuditRepository(settings.audit_dsn.get_secret_value(), settings=settings)
        self.vectors = vectors or VectorStore(
            settings.qdrant_url, settings.qdrant_collection, settings.embedding_dimension
        )
        self.processor = processor
        self.staging = StagingStore(settings.data_dir)
        self.stop = threading.Event()
        self.notify = notify
        self.supervisor = JobSupervisor(settings)
        lessons = LessonService(settings, LessonRepository(self.jobs.dsn), self.jobs, self.staging, self.audit)
        self.documents = DocumentService(self.jobs, self.vectors, self.audit, lessons=lessons)
        self.job_control = JobService(self.jobs, self.vectors, self.audit, self.staging)

    def _notify(self, *event):
        if self.notify is not None:
            self.notify(*event)

    def _record_failure(self, job, error):
        """Persist an outcome only while this attempt still owns the job row.

        The caller holds the artifact lock and any supervised writer has exited.
        A concurrent cancellation, publication or reclaim always takes precedence.
        """
        job_id, token = str(job["job_id"]), str(job["lease_token"])
        code, retryable = classify_error(error)
        if code in {"AI_DISABLED", "OPERATIONS_STORAGE_UNAVAILABLE"}:
            try:
                with self.jobs.owned_transaction(job_id, token) as conn:
                    self.jobs.pause_for_control(conn, job_id, token, code)
            except (LeaseLost, psycopg.Error):
                pass
            return
        retry = retryable and job["attempts"] < self.settings.max_attempts
        try:
            with self.jobs.owned_transaction(job_id, token) as conn:
                self.vectors.remove_attempt(str(job["document_id"]), token)
                self.audit.record(job, "RETRY" if retry else "FAILED", {"code": code}, attempt=True)
                self.jobs.fail(
                    conn, job_id, token, code, retry, min(300, 10 * 2 ** min(8, job["attempts"] - 1))
                )
        except LeaseLost:
            return
        except Exception:
            # A failed/uncertain commit remains recoverable through the lease.
            log.error("Job %s outcome persistence unavailable; lease recovery required", job_id)
            return
        log.warning("Job %s %s: %s", job_id, "retrying" if retry else "failed", code)

    def supervise(self, job):
        """The service path: every claimed job runs in a fresh, bounded process."""
        job_id = str(job["job_id"])
        if job["attempts"] > self.settings.max_attempts:
            code = "RETRY_LIMIT_REACHED"
        else:
            result = self.supervisor.run(
                job, self.stop, heartbeat=lambda: Path("/tmp/upraise-worker-heartbeat").touch()
            )
            if result.reason == "WORKER_STOPPED":
                return  # Durable lease recovery resumes this job after restart.
            # Even exit code zero may follow an unavailable outcome commit. The
            # fenced transaction preserves DONE/FAILED/retry/cancelled/reclaimed jobs.
            code = None if result.reason == "LEASE_LOST" else result.reason or "WORKER_PROCESS_EXITED"
        # Finalization uses external stores too. Bound it in a second process so a
        # database/vector outage cannot freeze the watchdog after stopping a parser.
        limit = min(self.settings.worker_phase_timeout_seconds, self.settings.lease_seconds)
        recovery_settings = self.settings.model_copy(update={
            "job_timeout_seconds": limit, "worker_startup_timeout_seconds": limit,
        })
        outcome = JobSupervisor(
            recovery_settings, child_target=run_recovery_child, check_ownership=False,
        ).run(
            {**job, "supervision_error": code}, self.stop,
            heartbeat=lambda: Path("/tmp/upraise-worker-heartbeat").touch(),
        )
        if outcome.reason:
            log.warning("Job %s outcome recovery deferred; durable state retained", job_id)

    def process(self, job):
        job_id = str(job["job_id"])
        try:
            with self.staging.artifact_lock(job_id):
                self._process(job)
        except IngestionError:
            log.warning("Job %s artifacts busy; lease recovery will retry", job_id)
        finally:
            try:
                self.job_control.complete_cleanup(job_id)
            except Exception:
                log.warning("Job %s cleanup deferred", job_id)

    def _process(self, job):
        job_id, token = str(job["job_id"]), str(job["lease_token"])
        document_id = str(job["document_id"])
        finished = threading.Event()
        lost = threading.Event()

        def heartbeat():
            while not finished.wait(self.settings.lease_seconds / 3):
                if self.notify is None:
                    Path("/tmp/upraise-worker-heartbeat").touch()
                try:
                    if not self.jobs.renew(job_id, token, self.settings.lease_seconds):
                        lost.set()
                        self._notify("lease_lost")
                        return
                except Exception:
                    lost.set()
                    self._notify("lease_lost")
                    return

        def progress(step, value, message):
            if lost.is_set():
                raise LeaseLost()
            self.jobs.progress(job_id, token, step, value)

        progress.set_workload = lambda phase, units: self._notify("workload", phase, units)

        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
        try:
            # Recheck ownership after the artifact lock; cancellation may follow claim.
            progress("parse", 0.05, "Starting preparation.")
            if job["attempts"] > self.settings.max_attempts:
                raise IngestionError("RETRY_LIMIT_REACHED")
            if job["pipeline_version"] != self.settings.pipeline_signature:
                raise IngestionError("PIPELINE_CONFIGURATION_CHANGED")
            self.audit.record(job, "ACCEPTED", {"pipeline_version": job["pipeline_version"]})
            metadata = IngestionMetadata.model_validate(job["metadata"])
            require_ai(self.settings, metadata.course_id)
            from upraise_ai.services.formats import FORMAT_MIMES

            processor = self.processor or get_processor(FORMAT_MIMES[metadata.source_format], self.settings)
            processed, embeddings = processor.prepare(
                self.staging.source_path(job_id),
                metadata,
                document_id,
                self.staging.job_dir(job_id),
                token,
                progress,
            )
            progress.set_workload("store", len(processed.chunks))
            progress("store", 0.93, "Publishing vectors.")
            with self.jobs.owned_transaction(job_id, token) as conn:
                count = self.vectors.write(processed, embeddings, token)
                result = {
                    **VersionCatalog(self.settings).references(),
                    "page_count": processed.page_count,
                    "chunk_count": count,
                    "warning_count": len(processed.warnings),
                    "pipeline_version": processed.pipeline_version,
                    "quality_status": processed.quality.status.value,
                    "quality_summary": processed.quality_summary,
                    # False means no flagged mismatch; UNVERIFIED still requires
                    # a consumer to decide whether the material is suitable.
                    "requires_review": processed.quality.status is QualityStatus.NEEDS_REVIEW,
                }
                if metadata.source_format == "csv":
                    result.update(source_format="csv", row_count=processed.row_count,
                                  column_count=processed.column_count, source_block_count=processed.source_block_count)
                elif metadata.source_format in {"doc", "docx"}:
                    result.update(source_format=metadata.source_format, source_block_count=processed.source_block_count)
                self.audit.record(job, "INDEX_VERIFIED", result, attempt=True)
                self.jobs.done(conn, job_id, token, result)
            # A DONE job + active_attempt is the authoritative activation boundary.
            # Qdrant cannot transact atomically with the job/audit databases.
            try:
                self.vectors.cleanup_old_attempts(document_id, token)
            except Exception:
                log.warning("Old vector attempt cleanup deferred for job %s", job_id)
            log.info("Job %s DONE (%d chunks)", job_id, count)
        except LeaseLost:
            log.info("Job %s lease transferred; stale worker stopped", job_id)
        except Exception as error:
            self._record_failure(job, error)
        finally:
            finished.set()
            thread.join(timeout=6)

    def run(self):
        while not self.stop.is_set():
            Path("/tmp/upraise-worker-heartbeat").touch()
            try:
                try:
                    self.documents.recover_deletion()
                except Exception:
                    log.warning("Document deletion pending; recovery will retry")
                self.job_control.recover_cleanup()
                controls = control_state(self.settings)
                if controls.killed():
                    self.stop.wait(self.settings.worker_poll_seconds)
                    continue
                blocked = controls.blocked_courses(set())
                filters = {"excluded_courses": blocked} if blocked else {}
                job = self.jobs.claim(self.settings.lease_seconds, self.settings.pipeline_signature, **filters)
                if job:
                    self.supervise(job)
                    continue
                if self.settings.artifact_retention_days:
                    for row in self.jobs.completed_before(self.settings.artifact_retention_days):
                        with self.staging.artifact_lock(str(row["job_id"])):
                            self.staging.remove(str(row["job_id"]))
            except ChildTerminationError:
                log.critical("Job writer survived termination; stopping worker")
                raise
            except Exception:
                log.error("Worker dependency unavailable; waiting to retry")
            self.stop.wait(self.settings.worker_poll_seconds)
        self.vectors.close()


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    worker = IngestionWorker(get_settings())
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: worker.stop.set())
    worker.run()


if __name__ == "__main__":
    main()
