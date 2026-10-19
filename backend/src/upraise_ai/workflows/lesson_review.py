"""Read retained text without reopening expired audio or unpublished A7 candidates."""


import xml.etree.ElementTree as ET

import psycopg
from fastapi import HTTPException

from upraise_ai.core.errors import IngestionError
from upraise_ai.schemas.lesson_review import ReviewLessonSummary, ReviewLessonText
from upraise_ai.services.lesson_pipeline import LessonPipeline
from upraise_ai.storage.lesson_archive import output_available, persistent_output


def review_summary(row, counts):
    metadata = row["metadata"]
    preferred = metadata.get("levels") or list(metadata.get("level_profiles", {}))
    levels = [level for level in preferred if level in counts]
    levels.extend(level for level in counts if level not in levels)
    if row["status"] != "DONE":
        reason = "LESSON_RESULT_NOT_READY"
    elif not output_available(row):
        reason = "LESSON_OUTPUT_EXPIRED"
    else:
        reason = None
    return ReviewLessonSummary(
        job_id=str(row["job_id"]), lesson_id=metadata["lesson_id"],
        course_id=metadata["course_id"], activity_id=metadata["activity_id"],
        language=metadata["language"], voice=metadata["voice"], job_status=row["status"],
        teaching_version=metadata.get("teaching_version", "v1"),
        levels=levels, chunk_counts=counts, created_at=row["created_at"],
        audio_expires_at=None if persistent_output(row) else row["expires_at"], revision_available=reason is None,
        revision_unavailable_reason=reason,
    )


def list_reviews(lessons, principal, *, course_id=None, limit=100, offset=0):
    if course_id is not None:
        principal.require_course(course_id)
    rows = lessons.jobs.list_reviewable(
        principal.client_id, None if principal.local_test else principal.courses, course_id, limit, offset,
    )
    return [review_summary(row, row["review_counts"]) for row in rows]


def review_text(lessons, job_id, principal):
    job_id = str(job_id)
    lessons.authorized(job_id, principal)
    # Share the publication/deletion lock and read all levels in one transaction.
    # Audio cleanup is independent: no manifest/WAV is read or recreated here.
    with lessons.staging.artifact_lock(job_id, shared=True), lessons.jobs.job_transaction(job_id) as (conn, row):
        if (not row or row["client_id"] != principal.client_id or row["content_deletion_requested_at"]
                or not principal.can_access_course(row["metadata"]["course_id"])
                or row["metadata"].get("revision_request")
                or (row.get("result") or {}).get("replacement_target_job_id")):
            raise HTTPException(404, "JOB_NOT_FOUND")
        if conn.execute("""SELECT 1 FROM lesson_content_deletions
            WHERE job_id=%s AND completed_at IS NULL LIMIT 1""", (job_id,)).fetchone():
            raise IngestionError("LESSON_DELETION_PENDING", status=503, retryable=True)
        if not row["metadata"].get("document_ids"):
            raise HTTPException(404, "LESSON_TEXT_NOT_FOUND")
        checker = LessonPipeline(lessons.settings, lessons.documents, None, lessons.staging, None, None)
        checker.check_sources(row, conn=conn)
        chunks = conn.execute("""SELECT chunk_id,chunk_index,text,ssml,sources,reviewer_report,level_key
            FROM prepared_lesson_chunks WHERE job_id=%s ORDER BY level_key,chunk_index""", (job_id,)).fetchall()
        variants = {}
        for chunk in chunks:
            level = chunk["level_key"] or row["metadata"].get("options", {}).get("learner_level", "intermediate")
            if level in row["deleted_levels"]:
                continue
            variant = variants.setdefault(level, {"learner_level": level, "chunks": []})
            narration = None
            if row["metadata"].get("teaching_version") == "v2" and chunk["ssml"]:
                try:
                    narration = "".join(ET.fromstring(chunk["ssml"]).itertext())
                except ET.ParseError:
                    raise IngestionError("LESSON_PREPARED_TEXT_CONFLICT", status=503) from None
            variant["chunks"].append({
                "chunk_id": str(chunk["chunk_id"]), "index": chunk["chunk_index"], "text": chunk["text"],
                "narration_text": narration,
                "sources": chunk["sources"], "reviewer_report": chunk["reviewer_report"],
            })
        if not variants:
            raise HTTPException(404, "LESSON_TEXT_NOT_FOUND")
        summary = review_summary(row, {level: len(value["chunks"]) for level, value in variants.items()})
        result = ReviewLessonText(**summary.model_dump(), variants={level: variants[level] for level in summary.levels})
        try:
            lessons.audit.lesson_access(row, "TEACHING_TEXT_ACCESSED", {"chunk_counts": summary.chunk_counts})
        except psycopg.Error:
            raise IngestionError("AUDIT_STORAGE_UNAVAILABLE", status=503, retryable=True) from None
        return result
