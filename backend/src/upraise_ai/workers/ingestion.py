import logging
import signal
import threading
from pathlib import Path

import psycopg
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError, PartialCredentialsError
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse

from upraise_ai.core.config import get_settings
from upraise_ai.core.errors import IngestionError, LeaseLost
from upraise_ai.schemas.documents import IngestionMetadata
from upraise_ai.services.formats import get_processor
from upraise_ai.storage.audit import AuditRepository
from upraise_ai.storage.files import StagingStore
from upraise_ai.storage.jobs import JobRepository
from upraise_ai.storage.vectors import VectorStore
from upraise_ai.workflows.documents import DocumentService
from upraise_ai.workflows.jobs import JobService

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
    def __init__(self, settings, jobs=None, audit=None, vectors=None, processor=None):
        self.settings = settings
        self.jobs = jobs or JobRepository(settings.jobs_dsn.get_secret_value())
        self.audit = audit or AuditRepository(settings.audit_dsn.get_secret_value())
        self.vectors = vectors or VectorStore(
            settings.qdrant_url, settings.qdrant_collection, settings.embedding_dimension
        )
        self.processor = processor or get_processor("application/pdf", settings)
        self.staging = StagingStore(settings.data_dir)
        self.stop = threading.Event()
        self.documents = DocumentService(self.jobs, self.vectors, self.audit)
        self.job_control = JobService(self.jobs, self.vectors, self.audit, self.staging)

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
                Path("/tmp/upraise-worker-heartbeat").touch()
                try:
                    if not self.jobs.renew(job_id, token, self.settings.lease_seconds):
                        lost.set()
                        return
                except Exception:
                    lost.set()
                    return

        def progress(step, value, message):
            if lost.is_set():
                raise LeaseLost()
            self.jobs.progress(job_id, token, step, value)

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
            processed, embeddings = self.processor.prepare(
                self.staging.source_path(job_id),
                metadata,
                document_id,
                self.staging.job_dir(job_id),
                token,
                progress,
            )
            progress("store", 0.93, "Publishing vectors.")
            with self.jobs.owned_transaction(job_id, token) as conn:
                count = self.vectors.write(processed, embeddings, token)
                result = {
                    "page_count": processed.page_count,
                    "chunk_count": count,
                    "warning_count": len(processed.warnings),
                    "pipeline_version": processed.pipeline_version,
                }
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
            code, retryable = classify_error(error)
            retry = retryable and job["attempts"] < self.settings.max_attempts
            try:
                with self.jobs.owned_transaction(job_id, token) as conn:
                    self.vectors.remove_attempt(document_id, token)
                    self.audit.record(job, "RETRY" if retry else "FAILED", {"code": code}, attempt=True)
                    self.jobs.fail(
                        conn, job_id, token, code, retry, min(300, 10 * 2 ** min(8, job["attempts"] - 1))
                    )
            except LeaseLost:
                pass
            except Exception:
                # Leave the lease recoverable; never lose accepted work during a DB outage.
                log.error("Job %s outcome persistence unavailable; lease recovery required", job_id)
            log.warning("Job %s %s: %s", job_id, "retrying" if retry else "failed", code)
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
                job = self.jobs.claim(self.settings.lease_seconds, self.settings.pipeline_signature)
                if job:
                    self.process(job)
                    continue
                if self.settings.artifact_retention_days:
                    for row in self.jobs.completed_before(self.settings.artifact_retention_days):
                        with self.staging.artifact_lock(str(row["job_id"])):
                            self.staging.remove(str(row["job_id"]))
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
