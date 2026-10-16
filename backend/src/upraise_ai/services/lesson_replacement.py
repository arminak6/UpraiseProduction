"""Publish A7 into its existing lesson using a transactional artifact pointer."""

import hashlib
import json
import os
import shutil
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from upraise_ai.core.errors import IngestionError
from upraise_ai.schemas.aligned_lessons import AlignedLessonResult
from upraise_ai.schemas.lessons import LessonResult
from upraise_ai.services.lesson_models import merge_reviews
from upraise_ai.services.lesson_pipeline import write_json
from upraise_ai.storage.lesson_archive import output_available, persistent_output


def artifact_job(row):
    return str(UUID((row.get("result") or {}).get("artifact_job_id") or str(row["job_id"])))


def chunk_fingerprint(chunk):
    return hashlib.sha256(json.dumps(chunk.model_dump(mode="json", exclude={"audio_url"}),
                                     sort_keys=True).encode()).hexdigest()


def artifact_directory(staging, row):
    if persistent_output(row):
        if staging.archive is None:
            raise IngestionError("LESSON_ARCHIVE_NOT_CONFIGURED", status=503)
        return staging.archive.version(row["job_id"], artifact_job(row), row["result"]["result_sha256"])
    return staging.job_dir(artifact_job(row))


def load_current(staging, row):
    path = current_manifest(staging, row)
    if not path.is_file():
        raise IngestionError("LESSON_OUTPUT_UNAVAILABLE", status=503)
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != row["result"]["result_sha256"]:
        raise IngestionError("LESSON_RESULT_INTEGRITY_FAILURE", status=503)
    payload = json.loads(raw)
    return (AlignedLessonResult if "variants" in payload else LessonResult).model_validate(payload)


def variants(result):
    return result.variants if isinstance(result, AlignedLessonResult) else {result.learner_level: result}


def publish_replacement(jobs, staging, conn, job, revised, ttl, *, legacy=False):
    """Caller holds processing and target artifact locks, and processing ownership.

    Files are prepared before the DB pointer changes. PostgreSQL commits the
    pointer, matching text/SSML, and processing completion in one transaction.
    Any failure before commit leaves the currently served lesson unchanged.
    """
    request = job["metadata"]["revision_request"]
    target_id = str(request["parent_job_id"])
    target = conn.execute("SELECT * FROM lesson_jobs WHERE job_id=%s FOR UPDATE", (target_id,)).fetchone()
    if (not target or target["client_id"] != job["client_id"]
            or target["metadata"]["course_id"] != job["metadata"]["course_id"]
            or not output_available(target)):
        raise IngestionError("REVISION_TARGET_UNAVAILABLE", status=409)
    levels = (set(request.get("target_chunk_sha256_by_level", {})) if request.get("apply_to_all_levels")
              else {job["metadata"]["options"]["learner_level"]})
    if levels.intersection(target["deleted_levels"]):
        raise IngestionError("REVISION_TARGET_LEVEL_DELETED", status=409)
    if conn.execute("""SELECT 1 FROM lesson_content_deletions
        WHERE job_id=%s AND completed_at IS NULL LIMIT 1""", (target_id,)).fetchone():
        raise IngestionError("LESSON_DELETION_PENDING", status=503, retryable=True)
    current = load_current(staging, target)
    selected = next(chunk for chunk in revised.chunks if chunk.chunk_id == str(request["chunk_id"]))
    all_levels = request.get("apply_to_all_levels", False)
    revised_variants, current_variants = variants(revised), variants(current)
    if all_levels and (not isinstance(revised, AlignedLessonResult) or not isinstance(current, AlignedLessonResult)
                       or set(revised_variants) != set(current_variants)
                       or set(request.get("target_chunk_sha256_by_level", {})) != set(current_variants)):
        raise IngestionError("REVISION_SNAPSHOT_INTEGRITY_FAILURE")
    changed_ids, chunk_ids = set(), {}
    for level, revised_variant in revised_variants.items():
        current_variant = current_variants.get(level)
        if current_variant is None:
            raise IngestionError("LESSON_LEVEL_NOT_FOUND", status=404)
        old_chunks = {chunk.chunk_id: chunk for chunk in current_variant.chunks}
        changed = (next((chunk for chunk in revised_variant.chunks
                        if chunk.content_unit_id == selected.content_unit_id), None) if all_levels else selected)
        if changed is None:
            raise IngestionError("REVISION_SNAPSHOT_INTEGRITY_FAILURE")
        original = old_chunks.get(changed.chunk_id)
        if original is None:
            raise IngestionError("LESSON_CHUNK_NOT_FOUND", status=404)
        expected = (request["target_chunk_sha256_by_level"][level] if all_levels
                    else request.get("target_chunk_sha256"))
        if expected and chunk_fingerprint(original) != expected:
            raise IngestionError("REVISION_TARGET_CHANGED", status=409)
        changes = revised_variant.chunks if legacy else [changed]
        if legacy and set(old_chunks) != {chunk.chunk_id for chunk in changes}:
            raise IngestionError("REVISION_SNAPSHOT_INTEGRITY_FAILURE")
        for replacement in changes:
            original = old_chunks.get(replacement.chunk_id)
            if (original is None or original.index != replacement.index
                    or original.content_unit_id != replacement.content_unit_id):
                raise IngestionError("REVISION_SNAPSHOT_INTEGRITY_FAILURE")
            prepared = conn.execute("SELECT * FROM prepared_lesson_chunks WHERE job_id=%s AND chunk_id=%s FOR UPDATE",
                                    (target_id, replacement.chunk_id)).fetchone()
            if (not prepared or prepared["text"] != original.text or prepared["ssml"] != original.ssml
                    or prepared["ssml_parts"] != original.ssml_parts):
                raise IngestionError("LESSON_PREPARED_TEXT_CONFLICT", status=503)
            current_variant.chunks[original.index] = replacement.model_copy(deep=True)
            conn.execute("""UPDATE prepared_lesson_chunks SET text=%s,ssml=%s,ssml_parts=%s,
                sources=%s,reviewer_report=%s,revisions=%s,versions=%s,updated_at=clock_timestamp()
                WHERE job_id=%s AND chunk_id=%s""", (replacement.text, replacement.ssml,
                Jsonb(replacement.ssml_parts), Jsonb([source.model_dump(mode="json") for source in replacement.sources]),
                Jsonb(replacement.reviewer_report.model_dump(mode="json")), replacement.revisions,
                Jsonb(revised_variant.versions), target_id, replacement.chunk_id))
            changed_ids.add(replacement.chunk_id)
        chunk_ids[level] = changed.chunk_id
        current_variant.revision = None
        current_variant.versions.update(revised_variant.versions)
        current_variant.reviewer_report = merge_reviews([chunk.reviewer_report for chunk in current_variant.chunks])
    current.reviewer_report = merge_reviews([variant.reviewer_report for variant in variants(current).values()])
    current.reviewer_report.issues = list({json.dumps(issue.model_dump(), sort_keys=True): issue
                                         for issue in current.reviewer_report.issues}.values())
    folder = staging.job_dir(str(job["job_id"]))
    previous_folder = artifact_directory(staging, target)
    # A complete new artifact directory includes the other levels and the latest
    # unrelated edits, not the older copies in the request's temporary snapshot.
    for chunk in current.chunks:
        source = (folder if chunk.chunk_id in changed_ids else previous_folder) / f"{chunk.chunk_id}.wav"
        if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != chunk.audio_sha256:
            raise IngestionError("LESSON_AUDIO_INTEGRITY_FAILURE", status=503)
        destination = folder / source.name
        if source != destination:
            destination.unlink(missing_ok=True)
            try:
                os.link(source, destination)
            except OSError:
                shutil.copyfile(source, destination)
                with destination.open("r+b") as stream:
                    os.fsync(stream.fileno())
        chunk.audio_url = f"/v1/jobs/{target_id}/audio/{chunk.chunk_id}"
    expires = datetime.now(UTC) + timedelta(seconds=ttl)
    current.expires_at = expires.isoformat()
    for variant in variants(current).values():
        variant.expires_at = expires.isoformat()
        variant.job_id = target_id
        variant.revision = None
    current.job_id = target_id
    # Use a separate manifest: retries still need the completed staging result.
    write_json(folder / "current-result.json", current.model_dump(mode="json"))
    digest = hashlib.sha256((folder / "current-result.json").read_bytes()).hexdigest()
    if staging.archive:
        staging.archive.save(target_id, str(job["job_id"]), folder, "current-result.json", digest, current.chunks)
    previous = target["result"]
    cleanup = list(dict.fromkeys([*previous.get("replacement_cleanup_jobs", []), artifact_job(target)]))
    summary = {**previous, "result_sha256": digest, "artifact_job_id": str(job["job_id"]),
               "replacement_cleanup_jobs": cleanup, "last_replacement_job_id": str(job["job_id"]),
               "replacement_count": previous.get("replacement_count", 0) + 1,
               "review_approved": current.reviewer_report.approved,
               "review_issue_count": len(current.reviewer_report.issues), "requires_review": True}
    summary.pop("revision", None)
    if staging.archive:
        summary["persistent_output"] = True
    conn.execute("""UPDATE lesson_jobs SET result=%s,expires_at=%s,updated_at=clock_timestamp(),
        cleanup_reason=NULL,cleanup_requested_at=NULL,cleanup_completed_at=NULL,
        callback_status='NONE',callback_token=NULL,callback_until=NULL WHERE job_id=%s""",
        (Jsonb(summary), expires, target_id))
    # A7's durable job is progress/audit only; its prepared copies are temporary.
    conn.execute("DELETE FROM prepared_lesson_chunks WHERE job_id=%s", (job["job_id"],))
    conn.execute("DELETE FROM lesson_chunk_plans WHERE job_id=%s", (job["job_id"],))
    conn.execute("UPDATE lesson_jobs SET metadata=metadata-'revision_request' WHERE job_id=%s", (job["job_id"],))
    selected_level = next(level for level, variant in revised_variants.items()
                          if any(chunk.chunk_id == selected.chunk_id for chunk in variant.chunks))
    return {"replacement_target_job_id": target_id, "learner_level": selected_level,
            "apply_to_all_levels": all_levels, "revised_levels": list(revised_variants), "chunk_ids": chunk_ids,
            **{key: revised.versions["revision_" + key] for key in ("configuration_id", "build_id")
               if "revision_" + key in revised.versions},
            "chunk_id": selected.chunk_id, "result_sha256": digest, "lesson_status": "DRAFT",
            "requires_review": True, "chunk_count": len(current_variants[selected_level].chunks)}


def current_manifest(staging, row):
    folder = artifact_directory(staging, row)
    return folder / ("current-result.json" if row["result"].get("artifact_job_id") else "result.json")


def cleanup_replacements(jobs, staging, target_id):
    """Idempotent, durable removal of old lesson artifacts and staging snapshots."""
    with staging.artifact_lock(target_id), jobs.job_transaction(target_id) as (conn, row):
        if not row or not row.get("result"):
            return
        current_id = artifact_job(row)
        pending = row["result"].get("replacement_cleanup_jobs", [])
        for obsolete in pending:
            if obsolete == current_id:
                continue
            with (nullcontext() if obsolete == target_id else staging.artifact_lock(obsolete)):
                staging.remove(obsolete)
                if staging.archive:
                    staging.archive.remove(target_id, obsolete)
        if pending:
            # Keep only files serving the current lesson; remove request feedback,
            # old snapshot text, generated checkpoints and diagnostic responses.
            with staging.artifact_lock(current_id):
                current = load_current(staging, row)
                keep = {"current-result.json", *{f"{chunk.chunk_id}.wav" for chunk in current.chunks}}
                temporary = staging.job_dir(current_id)
                for path in temporary.iterdir() if temporary.exists() else []:
                    if path.name not in keep:
                        if path.is_dir():
                            shutil.rmtree(path)
                        else:
                            path.unlink(missing_ok=True)
                if staging.archive and persistent_output(row):
                    staging.archive.remove(target_id, current_id, keep_digest=row["result"]["result_sha256"])
            conn.execute("""UPDATE lesson_jobs SET result=result-'replacement_cleanup_jobs'
                WHERE job_id=%s""", (target_id,))


def consolidate_legacy_revisions(lessons):
    """One-time, idempotent consolidation of completed pre-replacement A7 jobs.

    Oldest first makes the latest completed/created snapshot of each level current.
    It uses retained results/audio, never providers. Other levels stay intact.
    """
    jobs, staging = lessons.jobs, lessons.staging
    with jobs.connect() as conn:
        candidates = [str(row["job_id"]) for row in conn.execute("""SELECT job_id FROM lesson_jobs
            WHERE status='DONE' AND metadata ? 'revision_request' AND cleanup_requested_at IS NULL
            AND expires_at>clock_timestamp() ORDER BY created_at,job_id""").fetchall()]
    migrated = []
    for job_id in candidates:
        job = jobs.get(job_id)
        request = job["metadata"].get("revision_request")
        if not request:
            continue
        target_id = str(request["parent_job_id"])
        visited = {job_id}
        while True:
            if target_id in visited:
                raise IngestionError("REVISION_LINEAGE_INVALID")
            visited.add(target_id)
            target = jobs.get(target_id)
            if not target or target["client_id"] != job["client_id"]:
                raise IngestionError("REVISION_LINEAGE_INVALID")
            parent = (target.get("result") or {}).get("replacement_target_job_id")
            if not parent:
                parent = target["metadata"].get("revision_request", {}).get("parent_job_id")
            if not parent:
                break
            target_id = str(parent)
        with staging.artifact_lock(job_id), staging.artifact_lock(target_id):
            # Verify source ownership/deletion/active attempt through existing guards.
            from upraise_ai.services.lesson_pipeline import LessonPipeline
            checker = LessonPipeline(lessons.settings, lessons.documents, None, staging, None, None)
            checker.check_sources(job)
            revised = load_current(staging, job)
            if target["cleanup_requested_at"] or target["expires_at"] <= datetime.now(UTC):
                # Its former bundle expired independently. Retain the currently
                # available level as the stable lesson, without reviving expired audio.
                promoted_id = str(uuid4())
                folder = staging.job_dir(promoted_id)
                folder.mkdir(mode=0o700)
                for chunk in revised.chunks:
                    source = staging.job_dir(job_id) / f"{chunk.chunk_id}.wav"
                    try:
                        os.link(source, folder / source.name)
                    except OSError:
                        shutil.copyfile(source, folder / source.name)
                promoted = {**job, "job_id": promoted_id, "metadata": {**job["metadata"],
                            "revision_request": {**request, "parent_job_id": job_id}}}
                promoted["metadata"]["revision_request"].pop("target_chunk_sha256", None)
                with jobs.job_transaction(job_id) as (conn, fresh):
                    jobs.accept_revision(conn, promoted_id, job["client_id"], job["request_fingerprint"],
                                         promoted["metadata"], job["pipeline_version"])
                    ttl = max(1, int((fresh["expires_at"] - datetime.now(UTC)).total_seconds()))
                    summary = publish_replacement(jobs, staging, conn, promoted, revised, ttl, legacy=True)
                    lessons.audit.lesson(job, "CHUNK_REPLACED", {"mode": "legacy_consolidation", "target_job_id": job_id})
                    conn.execute("""UPDATE lesson_jobs SET result=%s,status='DONE',step='DONE',progress=1,
                        expires_at=%s,completed_at=clock_timestamp() WHERE job_id=%s""",
                        (Jsonb(summary), fresh["expires_at"], promoted_id))
                    conn.execute("UPDATE lesson_jobs SET metadata=metadata-'revision_request' WHERE job_id=%s", (job_id,))
                    key = revised.learner_level if target["metadata"].get("generation_version") else ""
                    conn.execute("DELETE FROM prepared_lesson_chunks WHERE job_id=%s AND level_key=%s", (target_id, key))
                target_id = job_id
            else:
                job["metadata"]["revision_request"] = {**request, "parent_job_id": target_id}
                job["metadata"]["revision_request"].pop("target_chunk_sha256", None)
                with jobs.job_transaction(job_id) as (conn, _):
                    ttl = max(1, int((job["expires_at"] - datetime.now(UTC)).total_seconds()))
                    summary = publish_replacement(jobs, staging, conn, job, revised, ttl, legacy=True)
                    lessons.audit.lesson(job, "CHUNK_REPLACED", {"mode": "legacy_consolidation", "target_job_id": target_id})
                    conn.execute("UPDATE lesson_jobs SET result=%s WHERE job_id=%s", (Jsonb(summary), job_id))
        if (jobs.get(target_id).get("result") or {}).get("replacement_cleanup_jobs"):
            cleanup_replacements(jobs, staging, target_id)
        migrated.append(job_id)
    return migrated
