"""A7 stages a full immutable lesson snapshot before making a revision job visible."""

import hashlib
import logging
import os
import shutil
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import HTTPException

from upraise_ai.core.errors import IngestionError
from upraise_ai.guards.operations import require_feature
from upraise_ai.schemas.lessons import LessonInput
from upraise_ai.services.lesson_pipeline import LessonPipeline, write_json

log = logging.getLogger("upraise.revisions")


def submit_revision(lessons, data, principal):
    parent_id = str(data.lesson_job_id)
    lessons.authorized(parent_id, principal)
    job_id = str(uuid4())
    staged = False
    try:
        with lessons.staging.artifact_lock(parent_id, shared=True), lessons.jobs.job_transaction(parent_id) as (conn, row):
            if (not row or row["client_id"] != principal.client_id
                    or not principal.can_access_course(row["metadata"]["course_id"])):
                raise HTTPException(404, "JOB_NOT_FOUND")
            require_feature(lessons.settings, "generation", row["metadata"]["course_id"])
            require_feature(lessons.settings, "tts", row["metadata"]["course_id"])
            result = lessons.result(row)
            selected = next((chunk for chunk in result.chunks if chunk.chunk_id == str(data.chunk_id)), None)
            if selected is None:
                raise HTTPException(404, "LESSON_CHUNK_NOT_FOUND")
            if not selected.sources:
                raise IngestionError("REVISION_SOURCE_UNAVAILABLE", status=409)
            if len(selected.text) > 16000:
                raise IngestionError("REVISION_CONTEXT_TOO_LARGE", status=422)
            prepared = {str(chunk["chunk_id"]): chunk for chunk in lessons.jobs.prepared_chunks(parent_id)}
            if len(prepared) != len(result.chunks):
                raise IngestionError("LESSON_PREPARED_TEXT_CONFLICT", status=503)
            for chunk in result.chunks:
                saved = prepared.get(chunk.chunk_id)
                if (not saved or saved["text"] != chunk.text or saved["ssml"] != chunk.ssml
                        or saved["ssml_parts"] != chunk.ssml_parts or saved["chunk_index"] != chunk.index
                        or saved["sources"] != [source.model_dump() for source in chunk.sources]):
                    raise IngestionError("LESSON_PREPARED_TEXT_CONFLICT", status=503)
            checker = LessonPipeline(lessons.settings, lessons.documents, None, lessons.staging, None, None)
            checker.check_sources(row, conn=conn)
            attempts = {document_id: str(conn.execute(
                "SELECT active_attempt FROM ingestion_jobs WHERE document_id=%s", (document_id,)
            ).fetchone()["active_attempt"]) for document_id in row["metadata"]["document_ids"]}
            folder = lessons.staging.job_dir(job_id)
            folder.mkdir(mode=0o700)
            staged = True
            # DONE artifacts are immutable. Hard links avoid copying unchanged
            # WAVs; atomic replacement of the edited file never changes a parent.
            for chunk in result.chunks:
                source = lessons.staging.job_dir(parent_id) / f"{chunk.chunk_id}.wav"
                if not source.is_file():
                    raise IngestionError("LESSON_AUDIO_UNAVAILABLE", status=503)
                with source.open("rb") as stream:
                    if hashlib.file_digest(stream, "sha256").hexdigest() != chunk.audio_sha256:
                        raise IngestionError("LESSON_AUDIO_INTEGRITY_FAILURE", status=503)
                target = folder / source.name
                try:
                    os.link(source, target)
                except OSError:
                    shutil.copyfile(source, target)
                    with target.open("r+b") as stream:
                        os.fsync(stream.fileno())
            snapshot = folder / "revision-base.json"
            write_json(snapshot, result.model_dump(mode="json"))
            metadata = LessonInput.model_validate({
                key: value for key, value in row["metadata"].items() if key != "revision_request"
            }).model_dump(mode="json")
            metadata["callback_url"] = None  # A7 uses A4/A5 pull delivery.
            metadata["revision_request"] = {
                "parent_job_id": parent_id, "chunk_id": str(data.chunk_id), "teacher_review": data.teacher_review,
                "number": (result.revision.number if result.revision else 0) + 1,
                "snapshot_sha256": hashlib.sha256(snapshot.read_bytes()).hexdigest(), "source_attempts": attempts,
            }
            if row["expires_at"] <= datetime.now(UTC):
                raise IngestionError("LESSON_OUTPUT_EXPIRED", status=410)
            lessons.jobs.accept_revision(conn, job_id, principal.client_id,
                                         metadata["revision_request"]["snapshot_sha256"], metadata,
                                         lessons.settings.lesson_signature)
        return {"job_id": job_id}
    except BaseException:
        if staged:
            # A lost commit acknowledgement must not remove a job that became
            # visible. On an unverifiable DB outcome retain artifacts for recovery.
            try:
                if lessons.jobs.get(job_id) is None:
                    lessons.staging.remove(job_id)
            except Exception:
                log.warning("Revision %s staging cleanup deferred", job_id)
        raise
