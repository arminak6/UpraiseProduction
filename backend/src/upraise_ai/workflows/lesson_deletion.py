"""B7: durable generated-content deletion, independent of ingested sources."""

import hashlib
import os
import shutil
from contextlib import nullcontext
from uuid import uuid4

from psycopg.types.json import Jsonb

from upraise_ai.core.errors import IngestionError
from upraise_ai.schemas.aligned_lessons import AlignedLessonResult
from upraise_ai.services.lesson_models import merge_reviews
from upraise_ai.services.lesson_pipeline import write_json
from upraise_ai.services.lesson_replacement import artifact_directory, artifact_job, load_current
from upraise_ai.storage.lesson_archive import output_available, persistent_output

DELETION_VERSION = "generated-content-deletion-v1"
DELETION_REASON = "GENERATED_LESSON_DELETED"


def artifact_ids(row):
    summary = row.get("result") or {}
    return list(dict.fromkeys([str(row["job_id"]), artifact_job(row),
                              *summary.get("replacement_cleanup_jobs", [])]))


def known_levels(row):
    metadata, result = row["metadata"], row.get("result") or {}
    return list(dict.fromkeys([*result.get("levels", []), *metadata.get("levels", []),
        *row.get("deleted_levels", []), *([] if metadata.get("levels") else
            [metadata.get("options", {}).get("learner_level", "intermediate")])]))


def begin_generated_deletion(lessons, job_id, level, principal):
    """Commit the fence before attempting locks/files; repeated calls reuse it."""
    with lessons.jobs.job_transaction(job_id) as (conn, row):
        if (not row or row["client_id"] != principal.client_id
                or not principal.can_access_course(row["metadata"]["course_id"])):
            raise IngestionError("JOB_NOT_FOUND", status=404)
        key = level or "*"
        previous = conn.execute("SELECT * FROM lesson_content_deletions WHERE job_id=%s AND level_key=%s",
                                (job_id, key)).fetchone()
        if previous:
            return previous["completed_at"] is None
        if row.get("content_deletion_requested_at"):
            if row["content_deletion_reason"] == DELETION_REASON:
                return row["content_deleted_at"] is None
            raise IngestionError("JOB_NOT_FOUND", status=404)
        if row["metadata"].get("revision_request") or (row.get("result") or {}).get("replacement_target_job_id"):
            raise IngestionError("LESSON_PROCESSING_JOB_NOT_DELETABLE", status=409)
        levels = known_levels(row)
        if level and level not in levels:
            raise IngestionError("LESSON_LEVEL_NOT_FOUND", status=404)
        if level in row.get("deleted_levels", []):
            return False
        if level and row["status"] in {"QUEUED", "RUNNING"}:
            raise IngestionError("LESSON_DELETION_NOT_READY", status=409)
        remaining = [name for name in levels if name not in row["deleted_levels"]]
        selected = [level] if level else remaining
        whole = not level or set(remaining).issubset(selected)
        conn.execute("""INSERT INTO lesson_content_deletions(deletion_id,job_id,level_key,levels,artifact_ids)
            VALUES (%s,%s,%s,%s,%s)""", (uuid4(), job_id, key, selected, artifact_ids(row)))
        conn.execute("""UPDATE lesson_jobs SET deleted_levels=%s,updated_at=clock_timestamp(),
            callback_token=NULL,callback_until=NULL,
            callback_status=CASE WHEN callback_status IN ('PENDING','SENDING') THEN 'DISPOSED' ELSE callback_status END
            WHERE job_id=%s""", (list(dict.fromkeys([*row["deleted_levels"], *selected])), job_id))
        if whole:
            conn.execute("""UPDATE lesson_jobs SET content_deletion_requested_at=clock_timestamp(),
                content_deletion_reason=%s,cleanup_completed_at=NULL,
                cleanup_requested_at=coalesce(cleanup_requested_at,clock_timestamp()),
                cleanup_reason=coalesce(cleanup_reason,CASE WHEN status IN ('QUEUED','RUNNING') THEN 'CANCEL' ELSE 'RECEIVED' END),
                status=CASE WHEN status IN ('QUEUED','RUNNING') THEN 'CANCELLED' ELSE status END,
                step=CASE WHEN status IN ('QUEUED','RUNNING') THEN 'CANCELLED' ELSE step END,
                lease_token=NULL,lease_until=NULL WHERE job_id=%s""", (DELETION_REASON, job_id))
        return True


def revoke_revisions(lessons, job_id):
    # Never hold the parent DB row while waiting for a processing row: A7's
    # publication transaction locks processing first, then parent.
    with lessons.jobs.connect() as conn:
        conn.execute("SET LOCAL lock_timeout = '5s'")
        row = conn.execute("SELECT * FROM lesson_jobs WHERE job_id=%s", (job_id,)).fetchone()
        whole = bool(row["content_deletion_requested_at"])
        children = conn.execute("""SELECT * FROM lesson_jobs WHERE
            metadata->'revision_request'->>'parent_job_id'=%s AND
            (%s OR (metadata->'revision_request'->>'apply_to_all_levels')::boolean IS TRUE
                OR metadata->'options'->>'learner_level'=ANY(%s)) ORDER BY job_id FOR UPDATE""",
            (job_id, whole, row["deleted_levels"])).fetchall()
        for child in children:
            conn.execute("""UPDATE lesson_jobs SET content_deletion_requested_at=coalesce(
                content_deletion_requested_at,clock_timestamp()),content_deletion_reason=%s,
                cleanup_completed_at=NULL,cleanup_requested_at=coalesce(cleanup_requested_at,clock_timestamp()),
                cleanup_reason=coalesce(cleanup_reason,CASE WHEN status IN ('QUEUED','RUNNING') THEN 'CANCEL' ELSE 'RECEIVED' END),
                status=CASE WHEN status IN ('QUEUED','RUNNING') THEN 'CANCELLED' ELSE status END,
                step=CASE WHEN status IN ('QUEUED','RUNNING') THEN 'CANCELLED' ELSE step END,
                lease_token=NULL,lease_until=NULL,callback_token=NULL,callback_until=NULL,
                callback_status=CASE WHEN callback_status IN ('PENDING','SENDING') THEN 'DISPOSED' ELSE callback_status END
                WHERE job_id=%s""", (DELETION_REASON, child["job_id"]))
    for child in children:
        lessons._complete_output_cleanup(str(child["job_id"]))


def copy_remaining(lessons, row, operation, current):
    folder = lessons.staging.job_dir(str(operation["deletion_id"]))
    folder.mkdir(mode=0o700, exist_ok=True)
    previous = artifact_directory(lessons.staging, row)
    for chunk in current.chunks:
        source, target = previous / f"{chunk.chunk_id}.wav", folder / f"{chunk.chunk_id}.wav"
        if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != chunk.audio_sha256:
            raise IngestionError("LESSON_AUDIO_INTEGRITY_FAILURE", status=503)
        target.unlink(missing_ok=True)
        try:
            os.link(source, target)
        except OSError:
            shutil.copyfile(source, target)
            with target.open("r+b") as stream:
                os.fsync(stream.fileno())
    write_json(folder / "current-result.json", current.model_dump(mode="json"))
    digest = hashlib.sha256((folder / "current-result.json").read_bytes()).hexdigest()
    if lessons.staging.archive:
        lessons.staging.archive.save(str(row["job_id"]), str(operation["deletion_id"]), folder,
                                     "current-result.json", digest, current.chunks)
    return digest


def publish_deletion(lessons, job_id, deletion_id):
    with lessons.staging.artifact_lock(job_id), lessons.jobs.job_transaction(job_id) as (conn, row):
        operation = conn.execute("SELECT * FROM lesson_content_deletions WHERE deletion_id=%s FOR UPDATE",
                                 (deletion_id,)).fetchone()
        if operation["published_at"]:
            return
        obsolete = list(dict.fromkeys([*map(str, operation["artifact_ids"]), *artifact_ids(row)]))
        if row["content_deletion_requested_at"]:
            staged = conn.execute("SELECT deletion_id FROM lesson_content_deletions WHERE job_id=%s", (job_id,))
            obsolete.extend(str(value["deletion_id"]) for value in staged)
            lessons.jobs.erase_content(conn, job_id)
        else:
            level = operation["level_key"]
            summary = dict(row.get("result") or {})
            active = output_available(row)
            if active:
                current = load_current(lessons.staging, row)
                if not isinstance(current, AlignedLessonResult) or level not in current.variants:
                    raise IngestionError("LESSON_LEVEL_NOT_FOUND", status=503)
                del current.variants[level]
                current.reviewer_report = merge_reviews([variant.reviewer_report for variant in current.variants.values()])
                digest = copy_remaining(lessons, row, operation, current)
                summary.update(result_sha256=digest, artifact_job_id=str(deletion_id), levels=list(current.variants),
                    total_chunk_count=len(current.chunks),
                    chunk_counts={name: len(variant.chunks) for name, variant in current.variants.items()},
                    review_approved=current.reviewer_report.approved,
                    review_issue_count=len(current.reviewer_report.issues), requires_review=True)
                if lessons.staging.archive:
                    summary["persistent_output"] = True
            else:
                remaining = [name for name in summary.get("levels", known_levels(row)) if name != level]
                if summary:
                    summary["levels"] = remaining
                    counts = {name: count for name, count in summary.get("chunk_counts", {}).items() if name != level}
                    if counts:
                        summary.update(chunk_counts=counts, total_chunk_count=sum(counts.values()))
            conn.execute("DELETE FROM prepared_lesson_chunks WHERE job_id=%s AND level_key=%s", (job_id, level))
            if summary:
                summary["replacement_cleanup_jobs"] = obsolete
                conn.execute("UPDATE lesson_jobs SET result=%s,updated_at=clock_timestamp() WHERE job_id=%s",
                             (Jsonb(summary), job_id))
        obsolete.append(str(deletion_id))  # Failed staging/whole supersession also removes its directory.
        conn.execute("""UPDATE lesson_content_deletions SET published_at=clock_timestamp(),artifact_ids=%s
            WHERE deletion_id=%s""", (list(dict.fromkeys(obsolete)), deletion_id))


def finish_deletion(lessons, job_id, deletion_id):
    with lessons.staging.artifact_lock(job_id), lessons.jobs.job_transaction(job_id) as (conn, row):
        operation = conn.execute("SELECT * FROM lesson_content_deletions WHERE deletion_id=%s FOR UPDATE",
                                 (deletion_id,)).fetchone()
        if operation["completed_at"]:
            return
        active = row.get("result") and output_available(row)
        keep = artifact_job(row) if active else None
        removed = []
        for identifier in map(str, operation["artifact_ids"]):
            if identifier == keep:
                continue
            with nullcontext() if identifier == job_id else lessons.staging.artifact_lock(identifier):
                lessons.staging.remove(identifier)
                if lessons.staging.archive:
                    lessons.staging.archive.remove(job_id, identifier)
            removed.append(identifier)
        if row.get("result"):
            remaining = [value for value in row["result"].get("replacement_cleanup_jobs", []) if value not in removed]
            summary = dict(row["result"])
            if remaining:
                summary["replacement_cleanup_jobs"] = remaining
            else:
                summary.pop("replacement_cleanup_jobs", None)
            conn.execute("UPDATE lesson_jobs SET result=%s WHERE job_id=%s", (Jsonb(summary), job_id))
        lessons.audit.generated_content_deleted(row, operation)
        if row["content_deletion_requested_at"]:
            if lessons.staging.archive:
                lessons.staging.archive.remove(job_id)
            lessons.audit.lesson(row, "CONTENT_DELETED", {"course_id": row["metadata"]["course_id"],
                                                          "reason": row["content_deletion_reason"]})
            conn.execute("""UPDATE lesson_jobs SET content_deleted_at=clock_timestamp(),
                cleanup_completed_at=clock_timestamp() WHERE job_id=%s""", (job_id,))
        conn.execute("UPDATE lesson_content_deletions SET completed_at=clock_timestamp() WHERE deletion_id=%s",
                     (deletion_id,))
        if active and persistent_output(row) and lessons.staging.archive:
            lessons.staging.archive.remove(job_id, keep, keep_digest=row["result"]["result_sha256"])
            with lessons.staging.artifact_lock(keep):
                lessons.staging.remove(keep)


def complete_generated_deletions(lessons, job_id):
    if not lessons.jobs.pending_content_deletion(job_id):
        return
    revoke_revisions(lessons, job_id)
    with lessons.jobs.connect() as conn:
        pending = conn.execute("""SELECT deletion_id FROM lesson_content_deletions
            WHERE job_id=%s AND completed_at IS NULL ORDER BY requested_at,deletion_id""", (job_id,)).fetchall()
    for operation in pending:
        publish_deletion(lessons, job_id, operation["deletion_id"])
        finish_deletion(lessons, job_id, operation["deletion_id"])
