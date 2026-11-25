# A1 Moodle ingestion: implementation design

Status: approved and implemented, 2026-10-02. The user has approved PDF-only input, source
document language and reuse of the Uprise/v2 extraction pipeline, including
Nova Pro mathematical transcription and optional Nova Lite figure descriptions.
This explicitly resolves the earlier A1 LLM=No conflict for that enrichment.
The user subsequently confirmed Moodle source identity and durable job/audit
storage and instructed implementation. This document preserves the agreed
design; `a1.md` describes the actual wire contract, operation and limits.
On the same date the user explicitly removed the A1 Ingestion Guard and job_id
from the A1 response body. On 2026-10-06 the user restored job_id in that body,
matching the original Word response. The Ingestion Guard remains removed;
`api-baseline.md` records these decisions separately from the original inventory.

## Moodle source identity and delivery

Preserve the draft's source_ref meaning: the Moodle stored-file ID, represented
as an opaque string, for example "48127". The trusted Middleware identifies the
Moodle instance through integration configuration/caller identity; a file ID
must not be treated as globally unique across Moodle sites. course_id and
activity_id retain their original API meanings. source_ref identifies the
source record; content hash/version identify the submitted content revision.
Neither a filesystem path nor a temporary download URL replaces that identity.

For the current backend-to-module integration, multipart delivery sends
the PDF bytes alongside the original A1 fields to POST /v1/documents. The
Middleware resolves Moodle storage and checks professor/course permissions.
The AI Module validates the authenticated caller and authorized course scope,
stages the bytes and checks PDF readability, unsupported encryption and
size/page limits. Document instruction/active-content, expected-hash, PDF magic,
declared MIME/filename extension and source-host allowlist checks were removed
by the user's explicit decision. Signed-URL delivery is also implemented with
HTTPS/TLS, DNS pinning, network policy, timeouts and redirect rejection. The
source server enforces its URL authorization/signature when serving the file.

content_hash remains a required opaque source metadata string; Moodle's native
SHA-1 contenthash is accepted without verification. Actual SHA-256 is computed
internally as source_hash for extraction IDs, checkpoints and retry identity.
Qdrant retains both the declared content_hash and computed source_hash.
Moodle also supports pathnamehash-based identity internally, but the current
Word baseline specifies file ID, so the implementation preserves file ID.

## Durable jobs and separate processing worker

The implementation uses a separate worker service and durable PostgreSQL job
database, upraise_jobs, with a separate runtime role from upraise_audit. Both currently
share the existing PostgreSQL server and persisted infrastructure volume. This
adds operational job storage to the module: it is an explicit extension of the
diagram/draft's PostgreSQL-audit-only boundary, even with separate databases.
The explicit decision was received. Course chunks/vectors remain in
Qdrant; lessons and learner sessions are not stored in the job database.

The database-backed work queue lets workers claim available rows in a
short transaction using FOR UPDATE SKIP LOCKED, record a lease/attempt token,
then commit before extraction or model calls. Heartbeats renew the lease;
expired leases permit recovery after a crash. Updates and publication need
attempt-token checks, so a stale worker cannot finish a reclaimed job. Retry
transient provider/infrastructure failures with bounded backoff; unsupported or
unreadable input becomes a permanent failure with safe error information.
Job execution is at-least-once; writes must tolerate replay. This design does
not promise that expensive provider calls execute exactly once.

Job records persist IDs, submission identity, source reference/hash/version,
source language, pipeline version, state, current step, progress, attempts,
timestamps and safe error codes. They do not hold document text, PDF bytes,
model credentials or signed URLs. A4 exposes QUEUED/RUNNING/DONE/FAILED/CANCELLED state,
progress, safe errors and result counts; see `a1.md` for its implemented schema.

An optional Idempotency-Key header supports Middleware retries: repeating the
same key and request returns the same document ID and polling URL;
reusing the key for different bytes/metadata is a conflict. A new intentional
submission still creates a fresh document ID, matching the older pipeline.
Do not silently deduplicate all equal PDFs or source_ref values. Worker retries
retain the original document ID and deterministic chunk IDs.
Legacy accepted requests remain retry-compatible after this update. New request
fingerprints include actual bytes, so reusing a declared content_hash does not
allow different PDFs under the same key. Pipeline version is pdf-ingestion-v1.3;
recorded signatures also include the extraction/model configuration.

## Acceptance, completion and storage

1. INGRESS authenticates and authorizes the Middleware request.
2. Durably stage the PDF, check processor support/resource limits and compute
   internal source_hash. The caller's content_hash is not verified.
3. Commit the accepted job, then return HTTP 202 with {document_id, job_id} and
   Location pointing to A4. This means accepted work, not finished ingestion.
4. The worker performs source-faithful Docling extraction/OCR, Nova recovery,
   section-aware chunking and Titan embeddings without an Ingestion Guard.
5. Write document-scoped Qdrant points with the original course_id, document_id,
   page and section fields, plus richer provenance and the source language.
   Verify the complete point count before exposing the document as usable.
   A failure after a partial write must not expose an incomplete document;
   attempt-scoped replay/cleanup and the DONE/active_attempt boundary enforce this.
   A2 deletion markers additionally prevent activation; see a2.md.
6. Persist audit/completion reliably, then expose DONE through the original A4
   GET /v1/jobs/{job_id}. Coordinate audit retries; do not claim a cross-store
   atomic transaction. A4 reports progress and safe errors, with scope checks.

Restart recovery uses persistent staging alongside job metadata. The
shared persistent staging volume supports this Compose deployment, with the
storage interface allowing object storage when deployments span hosts. The
API stages bytes before accepting a signed-URL request. Moodle remains the
authoritative source. Source/crop/manifest storage is retained by default;
enabling terminal-job/completed-deletion artifact cleanup requires an agreed retention period. Job
records and Qdrant content follow their own retention/deletion policies.

## Extensible format boundary

services/pdf/ contains PDF-specific input support checks, parser, OCR, crops and
mathematical recovery. Document schemas, ingestion workflow, INGRESS, chunking,
embeddings, job execution, audit and vector storage remain shared outside that
package. The dispatcher selects the current PDF processor. PDF is
the only accepted format now; DOCX/CSV processors can later implement the same
structured-output interface without changing A1's route or response. Future
formats may need different chunking policies and provenance (CSV rows do not
have PDF page numbers); do not invent those mappings in this implementation.

## Integration details required for final deployment

Production INGRESS retains OAuth2 client credentials plus mTLS from the draft.
Its issuer/audience, trusted caller scope and certificate/TLS termination
configuration must be supplied. The approved local testing exception is recorded
in local-testing.md (explicit development mode only; production remains the
default). The real Moodle delivery/signing setup and operational
retention still require integration decisions. See a6.md for durable
cancellation and receipt cleanup. The Word file
is still absent from the workspace; its preserved draft inventory remains the
baseline until the authority is restored or the earlier draft is confirmed.

## References

- [Moodle File API internals](https://moodledev.io/docs/5.2/apis/subsystems/files/internals)
- [Moodle file_storage reference](https://phpdoc.moodledev.io/4.5/d3/ddd/classfile__storage.html)
- [PostgreSQL 17 SELECT locking clauses](https://www.postgresql.org/docs/17/sql-select.html)
- Original Word contract inventory: api-baseline.md.
- Corrected extraction review: ../../codex_helper/a1-review.md.
