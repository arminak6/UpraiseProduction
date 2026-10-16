# Persistent course output archive

Docker Compose mounts the repository-root `course_archive/` directory at
`/data/course_archive` in the API, ingestion worker and lesson generation/delivery
workers. This directory is outside `backend/` and ignored by Git. It survives
container replacement and temporary-volume cleanup. Non-Compose deployments can
set `UPRAISE_LESSON_ARCHIVE_DIR` to a writable directory separate from
`UPRAISE_DATA_DIR`; omitting it preserves the legacy temporary-output policy.
Before the first Compose start on a fresh checkout, create `course_archive/` and
make it writable by container UID 10001. The bind mount deliberately requires an
existing directory. Keep the configured archive path/mount present on all API and
worker services while saved courses exist.

Completed lessons are copied as verified, immutable packages before the database
publishes them. Each package contains only its result manifest and referenced WAV
files, under `<lesson-job-id>/<artifact-id>/<manifest-sha256>/`. Provider caches,
prompts, diagnostics, teacher feedback, source uploads and unpublished snapshots
are not part of the saved course package. PostgreSQL remains the authority for
ownership, current artifact ID, integrity hash, level deletion and availability.
Files are never served directly from the archive directory.

The delivery worker still cleans temporary files after the configured 24-hour
TTL. A completed lesson whose summary contains `persistent_output: true` stays
available through A5, A7, A8 and B1 after that cleanup. A5/A8 and the teacher-review
API omit the output expiry for saved courses; B1 sessions keep their normal
independent time limit. Source access, human approval in production, content
deletion, hash verification and revision concurrency checks still apply.

A7 publishes the revised manifest/audio package under the same stable course ID,
then removes its superseded package. It never regenerates unchanged audio just
to archive it. B7 partial deletion publishes only the remaining levels and erases
the obsolete package; whole-course or A2 source/course deletion removes all
packages owned by that lesson. A6 receipt withdraws delivery and clears temporary
files while leaving the saved package until explicit content deletion. Cancelling
or expiring an unpublished revision does not remove the current saved lesson.

At API startup, existing intact completed output is copied and marked
as saved, without model or TTS calls and without changing its result hash. This
backfill is idempotent and does not restore files already removed by earlier
cleanup. It can preserve intact packages whose temporary deadline has passed, but
never restores an explicitly received or deleted lesson. Failed copies remain
subject to their previous policy and are retried at
the next startup; failures are logged without course text or provider details.
New publications fail closed and can retry on archive storage outages.

There is no automatic archive expiry. Back up `course_archive/` together with
PostgreSQL; neither Git nor a container image contains these packages.
