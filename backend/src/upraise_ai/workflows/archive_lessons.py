"""Preserve existing available lesson packages without generating any new content."""

import logging

from psycopg.types.json import Jsonb

from upraise_ai.services.lesson_pipeline import LessonPipeline
from upraise_ai.services.lesson_replacement import artifact_job, current_manifest, load_current
from upraise_ai.storage.lesson_archive import persistent_output

log = logging.getLogger("upraise.lesson_archive")


def archive_available_outputs(lessons):
    if not lessons.staging.archive:
        return 0
    with lessons.jobs.connect() as conn:
        candidates = [str(row["job_id"]) for row in conn.execute("""SELECT job_id FROM lesson_jobs
            WHERE status='DONE' AND (cleanup_requested_at IS NULL OR cleanup_reason='TTL')
            AND content_deletion_requested_at IS NULL AND result ? 'result_sha256'
            AND metadata->'revision_request' IS NULL
            AND NOT coalesce(result ? 'replacement_target_job_id',false)
            AND NOT coalesce(result->>'persistent_output'='true',false)
            ORDER BY created_at,job_id""").fetchall()]
    copied = 0
    for job_id in candidates:
        try:
            with lessons.staging.artifact_lock(job_id), lessons.jobs.job_transaction(job_id) as (conn, row):
                if (not row or row["status"] != "DONE" or row["content_deletion_requested_at"]
                        or row["cleanup_reason"] not in {None, "TTL"} or persistent_output(row)):
                    continue
                if conn.execute("""SELECT 1 FROM lesson_content_deletions
                    WHERE job_id=%s AND completed_at IS NULL LIMIT 1""", (job_id,)).fetchone():
                    continue
                checker = LessonPipeline(lessons.settings, lessons.documents, None, lessons.staging, None, None)
                checker.check_sources(row, conn=conn)
                result = load_current(lessons.staging, row)
                if result.source_attempts:
                    checker.check_sources(row, result.source_attempts, conn=conn)
                manifest = current_manifest(lessons.staging, row)
                lessons.staging.archive.save(job_id, artifact_job(row), manifest.parent, manifest.name,
                                             row["result"]["result_sha256"], result.chunks)
                summary = {**row["result"], "persistent_output": True}
                conn.execute("UPDATE lesson_jobs SET result=%s WHERE job_id=%s", (Jsonb(summary), job_id))
                copied += 1
        except Exception:
            # No provider calls or hidden regeneration. Intact live output stays
            # available under its original policy until a later verified retry.
            log.warning("Lesson %s archive backfill deferred", job_id)
    return copied
