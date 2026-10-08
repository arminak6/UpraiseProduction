import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID

import psycopg
from fastapi import HTTPException

from upraise_ai.core.errors import IngestionError
from upraise_ai.guards.operations import require_feature
from upraise_ai.schemas.lesson_catalog import ReadyLesson
from upraise_ai.schemas.lessons import LessonResult
from upraise_ai.services.lesson_callback import validate_callback_config
from upraise_ai.services.lesson_pipeline import LessonPipeline


class LessonService:
    def __init__(self, settings, jobs, documents, staging, audit):
        self.settings, self.jobs, self.documents, self.staging, self.audit = settings, jobs, documents, staging, audit

    def submit(self, data, principal, key=None):
        principal.require_course(data.course_id)
        require_feature(self.settings, "generation", data.course_id)
        require_feature(self.settings, "tts", data.course_id)
        if key is not None and (not key.strip() or len(key) > 200):
            raise IngestionError("INVALID_IDEMPOTENCY_KEY", status=400)
        if data.callback_url:
            validate_callback_config(self.settings, data.callback_url)
        # ID-only requests cannot fetch unknown Moodle files. Known A1 jobs remain
        # queued until ingestion completes, on the independent ingestion worker.
        for document_id in data.document_ids:
            with self.documents.document_transaction(str(document_id)) as (_, row):
                if (not row or row["deletion_requested_at"] or row["client_id"] != principal.client_id
                        or row["metadata"]["course_id"] != data.course_id):
                    raise HTTPException(404, "DOCUMENT_NOT_FOUND")
                if row["status"] in {"FAILED", "CANCELLED"}:
                    raise IngestionError("DOCUMENT_INGESTION_FAILED", status=409)
        metadata = data.model_dump(mode="json")
        fingerprint = hashlib.sha256(json.dumps({"request": metadata,
                                                 "pipeline": self.settings.lesson_signature}, sort_keys=True).encode()).hexdigest()
        row = self.jobs.accept(principal.client_id, key, fingerprint, metadata, self.settings.lesson_signature)
        return {"job_id": str(row["job_id"])}

    def authorized(self, job_id, principal):
        row = self.jobs.get(str(job_id))
        if (not row or row["client_id"] != principal.client_id
                or not principal.can_access_course(row["metadata"]["course_id"])):
            raise HTTPException(404, "JOB_NOT_FOUND")
        return row

    def list_ready(self, principal, *, course_id=None, limit=100, offset=0):
        if course_id is not None:
            principal.require_course(course_id)
        rows = self.jobs.list_ready(principal.client_id,
                                    None if principal.local_test else principal.courses,
                                    course_id, limit, offset)
        return [ReadyLesson(
            job_id=str(row["job_id"]), lesson_id=row["metadata"]["lesson_id"],
            course_id=row["metadata"]["course_id"], activity_id=row["metadata"]["activity_id"],
            language=row["metadata"]["language"], voice=row["metadata"]["voice"],
            learner_level=row["metadata"].get("options", {}).get("learner_level", "intermediate"),
            chunk_count=row["result"]["chunk_count"], requires_review=True,
            review_issue_count=row["result"].get("review_issue_count", 0),
            created_at=row["created_at"], expires_at=row["expires_at"],
        ) for row in rows]

    def result(self, row):
        row = self.jobs.get(row["job_id"])
        if row["cleanup_requested_at"] or (row["expires_at"] and row["expires_at"] <= datetime.now(UTC)):
            raise IngestionError("LESSON_OUTPUT_EXPIRED", status=410)
        if row["status"] != "DONE":
            raise IngestionError("LESSON_RESULT_NOT_READY", status=409)
        # Use the same activation check as the worker without creating AWS clients.
        checker = LessonPipeline(self.settings, self.documents, None, self.staging, None, None)
        checker.check_sources(row)
        path = self.staging.job_dir(str(row["job_id"])) / "result.json"
        if not path.is_file():
            raise IngestionError("LESSON_OUTPUT_UNAVAILABLE", status=503)
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != row["result"]["result_sha256"]:
            raise IngestionError("LESSON_RESULT_INTEGRITY_FAILURE", status=503)
        result = LessonResult.model_validate_json(raw)
        result.expires_at = row["expires_at"].isoformat()
        base = self.settings.lesson_public_base_url.rstrip("/")
        for chunk in result.chunks:
            chunk.audio_url = base + f"/v1/jobs/{row['job_id']}/audio/{chunk.chunk_id}"
        return result

    def dispose(self, job_id, reason, principal):
        self.authorized(job_id, principal)
        with self.jobs.job_transaction(job_id) as (conn, row):
            if row["cleanup_reason"] and row["cleanup_reason"] != reason:
                raise IngestionError("JOB_ACTION_CONFLICT", status=409)
            if reason == "CANCEL" and row["status"] not in {"QUEUED", "RUNNING", "CANCELLED"}:
                raise IngestionError("JOB_NOT_CANCELLABLE", status=409)
            if reason == "RECEIVED" and row["status"] not in {"DONE", "FAILED"}:
                raise IngestionError("JOB_NOT_ACKNOWLEDGEABLE", status=409)
            conn.execute(
                """UPDATE lesson_jobs SET cleanup_reason=%s,cleanup_requested_at=clock_timestamp(),
                status=CASE WHEN %s='CANCEL' THEN 'CANCELLED' ELSE status END,
                step=CASE WHEN %s='CANCEL' THEN 'CANCELLED' ELSE step END,
                lease_token=NULL,lease_until=NULL,callback_token=NULL,callback_until=NULL,
                callback_status=CASE WHEN callback_status IN ('PENDING','SENDING') THEN 'DISPOSED' ELSE callback_status END,
                updated_at=clock_timestamp() WHERE job_id=%s AND cleanup_requested_at IS NULL""",
                (reason, reason, reason, job_id),
            )
        self.complete_cleanup(job_id)

    def complete_cleanup(self, job_id):
        # Always acquire artifacts before the DB row, matching generation/delivery.
        # A6 first commits its fence, then returns 503 if a writer has not stopped.
        with self.staging.artifact_lock(job_id), self.jobs.job_transaction(job_id) as (conn, row):
            if not row or not row["cleanup_requested_at"] or row["cleanup_completed_at"]:
                return
            self.staging.remove(job_id)
            self.audit.lesson(row, "OUTPUT_" + row["cleanup_reason"], {"reason": row["cleanup_reason"]})
            conn.execute(
                """UPDATE lesson_jobs SET cleanup_completed_at=clock_timestamp(),
                updated_at=clock_timestamp() WHERE job_id=%s""", (job_id,),
            )

    def audio(self, row, chunk_id):
        result = self.result(row)
        chunk_id = str(UUID(chunk_id))
        chunk = next((chunk for chunk in result.chunks if chunk.chunk_id == chunk_id), None)
        if chunk is None:
            raise HTTPException(404, "LESSON_AUDIO_NOT_FOUND")
        path = self.staging.job_dir(str(row["job_id"])) / f"{chunk_id}.wav"
        if not path.is_file():
            raise IngestionError("LESSON_AUDIO_UNAVAILABLE", status=503)
        with path.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != chunk.audio_sha256:
                raise IngestionError("LESSON_AUDIO_INTEGRITY_FAILURE", status=503)
        return path, chunk

    def audit_download(self, row, *, chunk=None, byte_count=None):
        details = {"mode": "pull", "result_sha256": row["result"]["result_sha256"]}
        event = "RESULT_ACCESSED"
        if chunk is not None:
            event = "AUDIO_ACCESSED"
            details.update(chunk_id=chunk.chunk_id, audio_sha256=chunk.audio_sha256, bytes=byte_count)
        try:
            self.audit.lesson_access(row, event, details)
        except psycopg.Error:
            raise IngestionError("AUDIT_STORAGE_UNAVAILABLE", status=503, retryable=True) from None
