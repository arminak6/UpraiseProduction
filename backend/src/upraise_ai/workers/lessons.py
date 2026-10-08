"""Dedicated, killable lesson worker; ingestion and API acceptance remain independent."""

import hashlib
import logging
import signal
import threading
from pathlib import Path

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from upraise_ai.core.config import get_settings
from upraise_ai.core.errors import IngestionError, LeaseLost
from upraise_ai.guards.operations import blocked_generation_courses, feature_enabled, require_feature
from upraise_ai.services.lesson_callback import CallbackClient
from upraise_ai.services.lesson_models import LessonModels
from upraise_ai.services.lesson_pipeline import LessonPipeline
from upraise_ai.services.lesson_speech import LessonSpeech
from upraise_ai.services.revision_pipeline import RevisionPipeline
from upraise_ai.storage.audit import AuditRepository
from upraise_ai.storage.files import StagingStore
from upraise_ai.storage.jobs import JobRepository
from upraise_ai.storage.lessons import LessonRepository
from upraise_ai.storage.operations import lesson_worker_heartbeat
from upraise_ai.storage.vectors import VectorStore
from upraise_ai.workers.ingestion import classify_error
from upraise_ai.workers.supervision import ChildTerminationError, JobSupervisor, prepare_child
from upraise_ai.workflows.lessons import LessonService

log = logging.getLogger("upraise.lessons")
HEARTBEAT = Path("/tmp/upraise-lesson-heartbeat")


def read_lesson_state(dsn, job_id, timeout):
    options = conninfo_to_dict(dsn).get("options", "")
    with psycopg.connect(dsn, row_factory=dict_row, connect_timeout=5,
                         options=options + f" -c statement_timeout={max(1, int(timeout*1000))}") as conn:
        return conn.execute(
            """SELECT status,lease_token,cleanup_requested_at AS deletion_requested_at,
            lease_until>clock_timestamp() AS owned FROM lesson_jobs WHERE job_id=%s""", (job_id,)
        ).fetchone()


def run_lesson_child(settings, job, events, parent_pid):
    prepare_child(parent_pid)
    events.send(("ready", __import__("os").getpid()))
    worker = LessonWorker(settings, notify=lambda *event: events.send(event))
    try:
        worker.process(job)
    finally:
        worker.vectors.close()
        events.close()


def run_lesson_recovery(settings, job, events, parent_pid):
    prepare_child(parent_pid)
    events.send(("ready", __import__("os").getpid()))
    worker = LessonWorker(settings)
    try:
        with worker.staging.artifact_lock(str(job["job_id"])):
            if job.get("supervision_error"):
                worker.failure(job, IngestionError(job["supervision_error"],
                                                   retryable=job["supervision_error"] != "RETRY_LIMIT_REACHED"))
        worker.control.complete_cleanup(str(job["job_id"]))
    finally:
        worker.vectors.close()
        events.close()


def run_callback_child(settings, job, events, parent_pid):
    prepare_child(parent_pid)
    events.send(("ready", __import__("os").getpid()))
    worker = LessonWorker(settings)
    success = False
    try:
        with worker.staging.artifact_lock(str(job["job_id"])):
            current = worker.jobs.get(job["job_id"])
            if (not current or current["cleanup_requested_at"]
                    or str(current["callback_token"]) != str(job["callback_token"])):
                return
            result = worker.control.result(current).model_dump(mode="json") if current["status"] == "DONE" else None
            CallbackClient(settings).deliver(current, result)
            success = True
            worker.audit.lesson(job, "CALLBACK_DELIVERED", {"attempts": job["callback_attempts"]})
    except Exception:
        # Keep URLs, OAuth responses and credentials out of logs and audit.
        log.warning("Lesson %s callback delivery deferred", job["job_id"])
    finally:
        worker.jobs.finish_callback(job, success, settings.callback_max_attempts)
        worker.vectors.close()
        events.close()


class LessonWorker:
    def __init__(self, settings, *, jobs=None, documents=None, audit=None, vectors=None,
                 models=None, speech=None, notify=None):
        self.settings = settings
        self.jobs = jobs or LessonRepository(settings.jobs_dsn.get_secret_value())
        self.documents = documents or JobRepository(settings.jobs_dsn.get_secret_value())
        self.audit = audit or AuditRepository(settings.audit_dsn.get_secret_value())
        self.vectors = vectors or VectorStore(settings.qdrant_url, settings.qdrant_collection, settings.embedding_dimension)
        self.staging = StagingStore(settings.data_dir)
        self.models, self.speech = models, speech
        self.notify = notify
        self.stop = threading.Event()
        self.control = LessonService(settings, self.jobs, self.documents, self.staging, self.audit)
        budgets = settings.model_copy(update={"job_timeout_seconds": settings.lesson_timeout_seconds})
        self.supervisor = JobSupervisor(budgets, child_target=run_lesson_child,
                                        read_state=lambda job_id, timeout: read_lesson_state(
                                            settings.jobs_dsn.get_secret_value(), job_id, timeout))

    def failure(self, job, error):
        code, retryable = classify_error(error)
        retry = retryable and job["attempts"] < self.settings.max_attempts
        try:
            with self.jobs.owned_transaction(job["job_id"], job["lease_token"]) as conn:
                self.audit.lesson(job, "RETRY" if retry else "FAILED", {"code": code}, attempt=True)
                self.jobs.fail(conn, job["job_id"], job["lease_token"], code, retry, self.settings.lesson_ttl_seconds)
        except LeaseLost:
            pass

    def process(self, job):
        job_id, token = str(job["job_id"]), str(job["lease_token"])
        finished = threading.Event()

        def heartbeat():
            while not finished.wait(self.settings.lease_seconds/3):
                try:
                    if self.jobs.renew(job_id, token, self.settings.lease_seconds):
                        continue
                except Exception:
                    pass
                if self.notify:
                    self.notify("lease_lost")
                return

        def progress(step, value):
            self.jobs.progress(job_id, token, step, value)

        progress.set_workload = lambda phase, units: self.notify and self.notify("workload", phase, units)
        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
        try:
            with self.staging.artifact_lock(job_id):
                try:
                    progress("SOURCE", 0.01)
                    require_feature(self.settings, "generation", job["metadata"]["course_id"])
                    require_feature(self.settings, "tts", job["metadata"]["course_id"])
                    if job["attempts"] > self.settings.max_attempts:
                        raise IngestionError("RETRY_LIMIT_REACHED")
                    if job["pipeline_version"] != self.settings.lesson_signature:
                        raise IngestionError("LESSON_CONFIGURATION_CHANGED")
                    if self.settings.aws_region not in {"eu-central-1", "eu-central-2", "eu-west-1", "eu-west-3",
                                                       "eu-north-1", "eu-south-1", "eu-south-2"}:
                        raise IngestionError("LESSON_EU_REGION_REQUIRED")
                    pipeline_class = RevisionPipeline if job["metadata"].get("revision_request") else LessonPipeline
                    pipeline = pipeline_class(self.settings, self.documents, self.vectors, self.staging, None, None)
                    documents = pipeline.load_documents(job)
                    if documents is None:
                        with self.jobs.owned_transaction(job_id, token) as conn:
                            self.jobs.wait_sources(conn, job_id, token, self.settings.lesson_source_wait_seconds,
                                                   self.settings.lesson_ttl_seconds)
                        return
                    self.audit.lesson(job, "ACCEPTED", {"pipeline_version": job["pipeline_version"]})
                    if job["metadata"].get("revision_request"):
                        revision = job["metadata"]["revision_request"]
                        self.audit.lesson(job, "REVISION_ACCEPTED", {key: revision[key]
                            for key in ("parent_job_id", "chunk_id", "number")})
                    pipeline.models = self.models or LessonModels(
                        self.settings, diagnostic_dir=self.staging.job_dir(job_id) / "model-diagnostics")
                    pipeline.speech = self.speech or LessonSpeech(self.settings, pipeline.models)
                    expected = {str(row["document_id"]): str(row["active_attempt"]) for row, _ in documents}

                    def save_text(chunks, versions):
                        with self.jobs.owned_transaction(job_id, token) as conn:
                            pipeline.check_sources(job, expected, conn)
                            self.jobs.save_prepared_text(conn, job_id, chunks, versions)

                    def save_speech(chunk_id, ssml, parts):
                        with self.jobs.owned_transaction(job_id, token) as conn:
                            pipeline.check_sources(job, expected, conn)
                            self.jobs.save_prepared_speech(conn, job_id, chunk_id, ssml, parts)

                    result = pipeline.prepare(job, documents, progress, save_text=save_text, save_speech=save_speech)
                    progress("PUBLISH", 0.98)
                    with self.jobs.owned_transaction(job_id, token) as conn:
                        pipeline.check_sources(job, expected, conn)
                        summary = {"lesson_id": job["metadata"]["lesson_id"], "lesson_status": "DRAFT",
                                   "chunk_count": len(result.chunks), "requires_review": True,
                                   "review_approved": result.reviewer_report.approved,
                                   "review_issue_count": len(result.reviewer_report.issues), "label": "AI-generated"}
                        if result.revision:
                            summary["revision"] = result.revision.model_dump(mode="json")
                        with (self.staging.job_dir(job_id) / "result.json").open("rb") as stream:
                            summary["result_sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
                        self.audit.lesson(job, "DRAFT_CREATED", summary, attempt=True)
                        if result.revision:
                            self.audit.lesson(job, "CHUNK_REVISED", {**result.revision.model_dump(mode="json"),
                                "result_sha256": summary["result_sha256"]}, attempt=True)
                        self.jobs.done(conn, job_id, token, summary, self.settings.lesson_ttl_seconds)
                except LeaseLost:
                    pass
                except Exception as error:
                    self.failure(job, error)
        except IngestionError:
            log.warning("Lesson %s artifacts busy; lease recovery retained", job_id)
        finally:
            finished.set()
            thread.join(timeout=6)
            try:
                self.control.complete_cleanup(job_id)
            except Exception:
                log.warning("Lesson %s output cleanup deferred", job_id)

    def supervise(self, job):
        result = self.supervisor.run(job, self.stop, heartbeat=lambda: lesson_worker_heartbeat(self.settings))
        if result.reason == "WORKER_STOPPED":
            return
        limit = min(self.settings.worker_phase_timeout_seconds, self.settings.lease_seconds)
        settings = self.settings.model_copy(update={"job_timeout_seconds": limit,
                                                    "worker_startup_timeout_seconds": limit})
        JobSupervisor(settings, child_target=run_lesson_recovery, check_ownership=False).run(
            {**job, "supervision_error": None if result.reason == "LEASE_LOST" else
             result.reason or "WORKER_PROCESS_EXITED"}, self.stop,
            heartbeat=lambda: lesson_worker_heartbeat(self.settings),
        )

    def run(self):
        try:
            while not self.stop.is_set():
                lesson_worker_heartbeat(self.settings)
                try:
                    if not (feature_enabled(self.settings, "generation") and feature_enabled(self.settings, "tts")):
                        self.stop.wait(self.settings.worker_poll_seconds)
                        continue
                    blocked = blocked_generation_courses(self.settings)
                    filters = {"excluded_courses": blocked} if blocked else {}
                    job = self.jobs.claim(self.settings.lease_seconds, self.settings.lesson_signature, **filters)
                    if job:
                        self.supervise(job)
                        continue
                except ChildTerminationError:
                    raise
                except Exception:
                    log.error("Lesson worker dependency unavailable; retrying")
                self.stop.wait(self.settings.worker_poll_seconds)
        finally:
            self.vectors.close()


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    worker = LessonWorker(get_settings())
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: worker.stop.set())
    worker.run()


if __name__ == "__main__":
    main()
