# Retained teaching text for teacher review

Frontend V2's **Review courses** section reads durable teaching text independently
of A5's temporary JSON/WAV artifacts. It does not extend expiry or restore audio.
Both endpoints use the existing ingress, client/course ownership
checks, source activation checks and `Cache-Control: no-store`.

`GET /v1/lesson-reviews?course_id=...&limit=100&offset=0` lists one entry per stable
lesson with retained text. Optional `course_id` must be authorized; limit is 1–100
and offset is 0–1,000,000. Ownership/source/deletion filters precede pagination;
ordering is newest creation/job ID first. Entries include lesson/course/activity
identifiers, language/voice, configured level order, per-level part counts, job
status, creation/audio-expiry timestamps, and revision availability. Text retained
after TTL or A6 receipt is included. Incomplete root generations with saved text
are also readable. A7 candidate/processing jobs and deleted content are excluded.

`GET /v1/lesson-reviews/{job_id}` returns that summary plus `variants`, keyed by
level, each containing `learner_level` and ordered `chunks`. Each chunk exposes
only `chunk_id`, zero-based `index`, `text`, `sources` and `reviewer_report`; it
contains no SSML, WAV URLs/hashes, provider checkpoints or teacher feedback.
Reads share the lesson publication/deletion lock and transaction, so current
replacement text and all levels form one consistent snapshot. Each read writes a
content-free `TEACHING_TEXT_ACCESSED` audit event with per-level chunk counts.

Unauthorized/missing/deleted lessons and A7 processing IDs return 404, unavailable
sources return 409, and pending content deletion returns retryable 503. Expired
audio alone does not prevent text reads. B7 partial deletion removes that level
from both endpoints; whole-lesson/source/course deletion removes the text.

`revision_available` is true for DONE lessons with available, undisposed output:
unexpired temporary files or a [persistent saved package](lesson-archive.md).
It is a UI eligibility hint: A7 still rechecks source, manifest, audio integrity,
ownership, operator controls and concurrent edits on submission/publication.
Otherwise `revision_unavailable_reason` is `LESSON_RESULT_NOT_READY` or
`LESSON_OUTPUT_EXPIRED`. Text reads never regenerate missing audio or restore
expired output that was not saved.
Revisions use the existing [A7 contract](a7.md); successful replacement is visible
under the same stable lesson ID. All text remains DRAFT; final approval is external.
