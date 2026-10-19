import hashlib
import json
from contextlib import nullcontext
from datetime import UTC, datetime
from uuid import UUID

import psycopg
from fastapi import HTTPException

from upraise_ai.core.errors import IngestionError
from upraise_ai.guards.operations import require_feature
from upraise_ai.schemas.aligned_lessons import AlignedLessonInput, AlignedLessonResult
from upraise_ai.schemas.lesson_catalog import ReadyLesson
from upraise_ai.schemas.lessons import LessonResult
from upraise_ai.services.lesson_callback import validate_callback_config
from upraise_ai.services.lesson_pipeline import LessonPipeline
from upraise_ai.services.lesson_replacement import (
    artifact_directory,
    artifact_job,
    cleanup_replacements,
    current_manifest,
)
from upraise_ai.storage.lesson_archive import output_expired, persistent_output


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
        # Preserve existing V1 request fingerprints; V2 is an explicit opt-in.
        if data.teaching_version == "v1":
            metadata.pop("teaching_version", None)
        aligned = isinstance(data, AlignedLessonInput)
        signature = self.settings.lesson_v2_signature if aligned else self.settings.lesson_signature
        if aligned:
            configured = self.settings.lesson_level_profiles
            selected = set(data.levels) if data.levels is not None else set(configured)
            if not selected.issubset(configured):
                raise IngestionError('LESSON_LEVEL_NOT_CONFIGURED', status=422)
            # Keep deployment order stable, and never plan at an unselected level.
            profiles = {level: profile for level, profile in configured.items() if level in selected}
            plan_level = (self.settings.lesson_plan_level if self.settings.lesson_plan_level in profiles
                          else next(iter(profiles)))
            metadata.update(generation_version='a3-v2', level_profiles=profiles,
                            plan_level=plan_level, levels=list(profiles))
            metadata['options']['learner_level'] = plan_level
        fingerprint = hashlib.sha256(json.dumps({"request": metadata,
                                                 "pipeline": signature}, sort_keys=True).encode()).hexdigest()
        row = self.jobs.accept(principal.client_id, key, fingerprint, metadata, signature)
        return {"job_id": str(row["job_id"])}

    def authorized(self, job_id, principal):
        row = self.jobs.get(str(job_id))
        if (not row or row.get("content_deletion_requested_at") or row["client_id"] != principal.client_id
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
            learner_level=row.get('catalog_level') or row["metadata"].get("options", {}).get("learner_level", "intermediate"),
            chunk_count=row["result"]["chunk_count"], requires_review=True,
            review_issue_count=row["result"].get("review_issue_count", 0),
            created_at=row["created_at"], expires_at=None if persistent_output(row) else row["expires_at"],
            revision=row["result"].get("revision"),
            generation_version=row['metadata'].get('generation_version'),
            chunk_plan_id=row['result'].get('chunk_plan_id'),
        ) for row in rows]

    def delivery_row(self, job_id, principal):
        row = self.authorized(job_id, principal)
        target = (row.get("result") or {}).get("replacement_target_job_id")
        level = None
        if target:
            if row["cleanup_requested_at"] or row["expires_at"] <= datetime.now(UTC):
                raise IngestionError("LESSON_OUTPUT_EXPIRED", status=410)
            level = row["result"]["learner_level"]
            row = self.authorized(target, principal)
        return row, level

    def result(self, row, learner_level=None):
        row = self.jobs.get(row["job_id"])
        if row.get("content_deletion_requested_at"):
            raise IngestionError("JOB_NOT_FOUND", status=404)
        if learner_level in row.get("deleted_levels", []):
            raise IngestionError("LESSON_LEVEL_NOT_FOUND", status=404)
        if self.jobs.pending_content_deletion(row["job_id"]):
            raise IngestionError("LESSON_DELETION_PENDING", status=503, retryable=True)
        if output_expired(row):
            raise IngestionError("LESSON_OUTPUT_EXPIRED", status=410)
        if row["status"] != "DONE":
            raise IngestionError("LESSON_RESULT_NOT_READY", status=409)
        target = row["result"].get("replacement_target_job_id")
        if target:
            current = self.jobs.get(target)
            if (not current or current["client_id"] != row["client_id"]
                    or current["metadata"]["course_id"] != row["metadata"]["course_id"]):
                raise IngestionError("JOB_NOT_FOUND", status=404)
            with self.staging.artifact_lock(target, shared=True):
                return self.result(current, learner_level or row["result"]["learner_level"])
        # Use the same activation check as the worker without creating AWS clients.
        checker = LessonPipeline(self.settings, self.documents, None, self.staging, None, None)
        checker.check_sources(row)
        path = current_manifest(self.staging, row)
        if not path.is_file():
            raise IngestionError("LESSON_OUTPUT_UNAVAILABLE", status=503)
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != row["result"]["result_sha256"]:
            raise IngestionError("LESSON_RESULT_INTEGRITY_FAILURE", status=503)
        payload = json.loads(raw)
        result = (AlignedLessonResult.model_validate(payload) if 'variants' in payload
                  else LessonResult.model_validate(payload))
        if result.source_attempts:
            checker.check_sources(row, result.source_attempts)
        result.expires_at = None if persistent_output(row) else row["expires_at"].isoformat()
        base = self.settings.lesson_public_base_url.rstrip("/")
        for chunk in result.chunks:
            chunk.audio_url = base + f"/v1/jobs/{row['job_id']}/audio/{chunk.chunk_id}"
        if isinstance(result, AlignedLessonResult):
            for variant in result.variants.values():
                variant.expires_at = result.expires_at
            if learner_level is not None:
                if learner_level not in result.variants:
                    raise IngestionError('LESSON_LEVEL_NOT_FOUND', status=404)
                return result.variants[learner_level]
        elif learner_level is not None and learner_level != result.learner_level:
            raise IngestionError('LESSON_LEVEL_NOT_FOUND', status=404)
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
        from upraise_ai.workflows.lesson_deletion import complete_generated_deletions
        complete_generated_deletions(self, str(job_id))
        self._complete_output_cleanup(job_id)

    def _complete_output_cleanup(self, job_id):
        current = self.jobs.get(job_id)
        if current and current.get("result"):
            target = current["result"].get("replacement_target_job_id") or str(job_id)
            if ((self.jobs.get(target) or {}).get("result") or {}).get("replacement_cleanup_jobs"):
                cleanup_replacements(self.jobs, self.staging, target)
        # Always acquire artifacts before the DB row, matching generation/delivery.
        # A6 first commits its fence, then returns 503 if a writer has not stopped.
        with self.staging.artifact_lock(job_id), self.jobs.job_transaction(job_id) as (conn, row):
            if not row or not row["cleanup_requested_at"] or row["cleanup_completed_at"]:
                return
            # Processing-job cleanup cannot delete files owned by a current
            # lesson. Stable lesson cleanup removes its active directory too.
            reference = conn.execute("""SELECT job_id FROM lesson_jobs WHERE
                result->>'artifact_job_id'=%s AND cleanup_requested_at IS NULL
                AND content_deletion_requested_at IS NULL LIMIT 1""", (str(job_id),)).fetchone()
            if not reference:
                self.staging.remove(job_id)
            if (self.staging.archive and not persistent_output(row)
                    and not row["metadata"].get("revision_request")):
                # Failed/unpublished candidates are not durable course output.
                self.staging.archive.remove(job_id)
            active = artifact_job(row) if row.get("result") else str(job_id)
            if active != str(job_id):
                with self.staging.artifact_lock(active):
                    self.staging.remove(active)
            if row["metadata"].get("revision_request"):
                if self.staging.archive:
                    parent_id = row["metadata"]["revision_request"]["parent_job_id"]
                    self.staging.archive.remove(parent_id, job_id)
                # Unpublished A7 text is a temporary candidate, not retained
                # teaching content. Cancel/receipt/expiry disposes it as well.
                conn.execute("DELETE FROM prepared_lesson_chunks WHERE job_id=%s", (job_id,))
                conn.execute("DELETE FROM lesson_chunk_plans WHERE job_id=%s", (job_id,))
                conn.execute("UPDATE lesson_jobs SET metadata=metadata-'revision_request',result=NULL WHERE job_id=%s", (job_id,))
            if (row.get("result") or {}).get("teaching_failure"):
                conn.execute("UPDATE lesson_jobs SET result=NULLIF(result-'teaching_failure','{}'::jsonb) WHERE job_id=%s",
                             (job_id,))
            if row.get("content_deletion_requested_at"):
                if self.staging.archive:
                    self.staging.archive.remove(job_id)
                # B7 artifact directories have durable deletion-operation IDs,
                # rather than processing-job rows. A2 must remove them too.
                for operation in conn.execute("SELECT deletion_id FROM lesson_content_deletions WHERE job_id=%s", (job_id,)):
                    identifier = str(operation["deletion_id"])
                    with nullcontext() if identifier == str(job_id) else self.staging.artifact_lock(identifier):
                        self.staging.remove(identifier)
                self.jobs.erase_content(conn, job_id)
                self.audit.lesson(row, "CONTENT_DELETED", {"course_id": row["metadata"]["course_id"],
                                                          "reason": row["content_deletion_reason"]})
                conn.execute("UPDATE lesson_jobs SET content_deleted_at=clock_timestamp() WHERE job_id=%s", (job_id,))
            else:
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
        row = self.jobs.get(str(row["job_id"]))
        if row["result"].get("replacement_target_job_id"):
            row = self.jobs.get(row["result"]["replacement_target_job_id"])
        path = artifact_directory(self.staging, row) / f"{chunk_id}.wav"
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
