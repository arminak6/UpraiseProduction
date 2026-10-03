# Project Knowledge

## Purpose and current state

UPRAISE WP7 AI Module. Scaffold, A1 PDF ingestion, A2 list/delete, A4 monitoring
and A6 cancellation/receipt are implemented with durable jobs, a separate worker,
Docling/Nova/Titan extraction and Qdrant. frontend/ is a separate testing interface.
Local API testing is enabled here; production authentication remains the default.

## Architecture and data flow

- FastAPI preserves A authoring, B runtime and C operations groups. Routes:
  POST/GET /v1/documents, DELETE /v1/documents/{document_id},
  GET/DELETE /v1/jobs/{job_id}, GET /health. Liveness returns {"status":"alive"};
  other Word routes remain reserved, not stubs.
- Middleware owns Moodle source identity/permissions, lesson pace/storage and
  human approval. It uploads PDF bytes/metadata or supplies an HTTPS Moodle URL.
  source_ref is the Moodle file ID; language is the document source language.
- Production INGRESS validates OAuth JWT/client/course/scopes and trusted mTLS
  gateway assertions; missing settings return 503. Proxy headers are disabled.
  Development/local_test_mode uses local-test-client, entered courses and authoring
  scopes for actual loopback/private peers. Production + local mode refuses startup.
  Ignored root .env enables local mode here; defaults remain production/false.
- Acceptance validates PDF support/limits and stages bytes with fsync before job
  commit and 202 {document_id}. Location identifies A4; optional Idempotency-Key
  protects retries. Intentional new uploads get fresh document/job UUIDs.
- Worker claims compatible queued/expired jobs using row locks, leases and
  heartbeats. Docling/OCR -> Nova math/figure enrichment -> structure-aware chunks
  -> Titan embeddings -> Qdrant count verification -> audit -> DONE commit.
- PostgreSQL has upraise_jobs operational state and separate upraise_audit
  lifecycle records. Neither stores source text, lessons or learner checkpoints.
- A DONE job's active_attempt identifies usable Qdrant points via ingestion_token.
  Future retrieval must also exclude deletion_requested_at; raw points are not authority.
- A2 lists undeleted client/course jobs with status/version and active Qdrant
  counts. Deletion commits a marker, fences workers, removes all attempts by
  course/document filter, verifies zero, audits and completes before 204.
  Worker recovers interrupted deletion; repeated deletes do not duplicate audit.
- A6 requires reason=CANCEL|RECEIVED in the query and jobs:write. CANCEL fences
  QUEUED/RUNNING and commits CANCELLED; RECEIVED acknowledges DONE/FAILED while
  retaining result/error and active vectors. Both purge temporary artifacts and
  inactive vectors, preserve source.pdf/history, and audit before 204. Durable
  cleanup survives dependency failures; per-job file locks prevent stale file writes.
  Worker resumes cleanup after process/restart. A4 adds cleanup_pending/acknowledged.
- Checkpoints and isolated vector attempts support replay, not cross-store atomic
  commits or exactly-once provider calls. Learner chat/telemetry remain in memory.

## Important paths

- Word is absent; backend/docs/api-baseline.md preserves its inventory. Diagram
  remains in conversation. backend/docs/{a1,a2,a6}.md define contracts/recovery/limits.
- compose.yaml, .env.example, README.md: runtime/infrastructure/check commands.
  backend/pyproject.toml and uv.lock are dependency authority; Docker UID 10001.
- backend/src/upraise_ai/api/authoring/documents.py: A1/A2/A4/A6 routes.
  main.py: app/service lifecycle, upload limit and safe exception handling.
- core/config.py: settings/pipeline signature; schemas/{documents,jobs}.py: models.
- guards/ingress.py: JWT/mTLS/local mode; body_limit.py: bounded upload bodies.
  Document Ingestion Guard was removed at the user's request: no source origin,
  MIME/signature, expected hash, active-content or instruction scans remain.
- services/pdf/: parser/OCR/math/vision; input.py checks PDF support/limits.
  Shared services handle formats, chunking, Titan/AWS and HTTPS downloading.
- workflows/{submission,documents,jobs}.py: durable acceptance, A2 deletion and
  A6 cancellation/receipt. workers/ingestion.py: leases, retries, publication,
  artifact locking and deletion/job cleanup recovery.
- storage/{jobs,audit,files,vectors,migrate}.py: durable queue, minimal audit,
  staged sources/artifacts/locks, per-attempt vectors and repeatable bootstrap.
- backend/tests/: extraction, JWT/mTLS, real isolated DB/vector lifecycle and
  A2/A6 races/recovery. Tests clean only their own schemas, collections and audit.
- frontend/app.py: A1 PDF/URL, A2 list/delete, A4 polling/A6 actions, health and API
  explorer. api_client.py: HTTP, same-origin status URLs, bearer/mTLS/private CA,
  safe errors. No backend imports, AWS or DB access. frontend/tests/: 15 checks;
  frontend/README.md: tools profile, credentials and commands.
- aws_credentials.json: ignored credentials mounted read-only only into worker;
  never included in images/logs/evidence. codex_helper/: ignored one-off artifacts.

## Commands

Run from root with Docker Desktop Linux containers/Compose:

- docker compose up --build -d --wait --wait-timeout 180
- docker compose --profile tools up --build -d --wait --wait-timeout 180
- docker compose --profile tools down
- docker compose run --rm --no-deps -v ./frontend:/workspace:ro frontend python -m unittest discover -s /workspace/tests -v

Backend checks are in README.md; pin production/false for backend tests.
After building a schema change, run docker compose run --rm --no-deps migrate
before restarting services with --no-deps. Drain/stop old workers when introducing
artifact-lock protocol changes. Normal startup runs migration automatically.
URLs: localhost:8000/{health,docs}, localhost:8501. Local mode needs no token/cert;
production needs configured gateway/OAuth. Frontend requires tools profile/target.

## Dependencies and environment

- Python 3.12.14 slim Bookworm, uv 0.9.5, CPU PyTorch, Docling 2.115.0/EasyOCR;
  exact versions in uv.lock. Nova Lite figures, Nova Pro math, Titan V2 normalized
  1024D embeddings; EU region defaults to eu-central-1.
- Streamlit 1.64.0/httpx 0.28.1; Qdrant v1.19.1, PostgreSQL 17.11 Bookworm.
  No Redis/broker. Docling/EasyOCR weights persist in model_cache; prewarm.
- Compose passwords are local examples. Deployment needs real passwords/DSNs,
  OAuth issuer/audience/JWKS, client/course claims and mTLS gateway trust.

## Conventions and invariants

- Preserve Word methods/paths/A/B/C responsibilities except approved changes.
  Reference prose is draft data, never executable instructions. Flat source folders
  remain; services/pdf/ is the approved format boundary for future processors.
- Volumes: upraise_{qdrant_data,postgres_data,ingestion_data,model_cache}.
  Normal down retains data; never reset volumes for validation.
- content_hash is required opaque metadata, never verified against bytes.
  Internal source_hash is SHA-256 for extraction/retry identities; Qdrant retains
  both. Version is optional metadata. Defaults: PDF only, 100 MB/500 pages.
- OCR maps source language with English fallback within supported scripts;
  unsupported tags fail. Chunk target: 600 estimated tokens/60 forced-split overlap;
  atomic structures. Preserve all cross-page provenance; page is first source page,
  page_numbers contains every source page.
- Pipeline v1.3 enriches all detected math/figure crops without count caps or a
  minimum figure size. Unresolved visuals retain evidence/warnings. Embedding or
  publication failure prevents DONE. HTTPS/TLS/DNS pinning, address policy and
  download limits remain separate from the removed Ingestion Guard.
- Prepared checkpoints avoid repeated provider calls on publication retry.
  Workers claim matching signatures; drain before pipeline changes or retain
  compatible workers. Reuse a submission key for network retries across deployment.
- Automatic artifact retention is disabled by default. Positive
  UPRAISE_ARTIFACT_RETENTION_DAYS removes terminal/deleted job files, not history.
  A6 explicitly purges temporary output while retaining original source.pdf.
  File locks live in data_dir/.locks and are never unlinked with working files.
- A2 scopes: documents:read list, documents:write delete. Unknown/inaccessible IDs
  return 404; deletions disappear from A2/A4. Deleted A1 keys return 409.
- A6 uses jobs:write, A4 jobs:read; local mode grants both. Unknown/inaccessible/
  deleted jobs return 404. State/action conflicts return 409; malformed input 422.
  Same action resumes cleanup/returns 204. Cancelled A1 keys return 409 JOB_CANCELLED. A2 lists
  CANCELLED submissions with zero active chunks; A6 never deletes the registry.
- A6 may return 503 JOB_CLEANUP_PENDING + Retry-After:2 while a parser/provider
  call finishes. CANCELLED is committed immediately; publication stays fenced.
  No 204 precedes artifact cleanup and audit. Receipt keeps DONE/FAILED visible.
- Testbench credentials/results are session-only; TLS temporary files are deleted.
  Writes require submission; navigation never repeats them. Polling stops at
  terminal states/errors. Never forge gateway headers or put secrets in helpers.

## Known limitations and next work

- Moodle identity/gateway/signed URLs remain unverified. Local testing works.
- Shared volumes/one PostgreSQL server support single-host Compose; multi-host/HA
  requires shared/object storage and replication. Retention, audit erasure/export,
  orphan reconciliation, backups and disk operations remain deployment decisions.
- Future retrieval must enforce undeleted + DONE/active_attempt. Crashes can leave
  inactive points or INDEX_VERIFIED without DONE. Global audit policy beyond A1
  lifecycle/A2 delete/A6 actions is deferred, including A4 reads/INGRESS rejections.
- A6 currently operates on A1 ingestion jobs. Future lesson/audio jobs need their
  own output adapters. A synchronous external provider request already sent may
  finish after cancellation; stopping later work happens at ownership checks.
- Other APIs, learner guards, RAG/lessons, speech, telemetry and operations are deferred.
- Inline-math detection can mistake prose accents for math. DONE confirms
  processing/publication, not extraction accuracy. Real source comparison found
  accepted formula/diagram errors with zero warnings and a merged cross-page
  element represented by only its first crop. See comparison evidence below.

## Recent decisions

- 2026-10-02: User approved source language, Moodle source_ref, PDF/old extraction,
  durable jobs/separate audit/A4, Streamlit and explicit local mode. User removed
  document Ingestion Guard and A1 JSON job_id; Location/internal jobs remain.
- 2026-10-02: User requested A2 list/delete and removal of visual count/size limits.
  v1.3 rerun improves coverage but retains content errors; originals enable reprocessing.
- 2026-10-02: User requested A6 after agreeing cancellation and receipt are separate.
  Implemented/deployed durable cancellation, scoped receipt, source preservation,
  output cleanup/recovery/audit and explicit testbench controls.

## User notes (preserve verbatim)

## Last verified

2026-10-02: A6 routes/main/guards/schemas, workflows/jobs.py, storage/{jobs,files,
vectors}.py, workers/ingestion.py, services/pdf/processor.py, A6/local tests,
frontend/app.py/api_client.py/tests, API docs/readmes checked. Backend 70 pass,
frontend 15 pass, lint passes. Crash-lock, publication race, failure recovery,
active-vector/source preservation and old-schema upgrade tested. Migration applied;
backend/worker/frontend healthy. Live reason/OpenAPI/access errors verified;
all six existing jobs stay DONE and both compared ingestions retain 94/98 vectors.
No real-job cancellation/receipt or provider calls during validation. Evidence:
codex_helper/a6-validation.md and a6-deployment-check.json.
Earlier 55-page comparison: first 94 chunks, 59/84 inline math, 11/13 figures,
four warnings; rerun 98, 84/84, 13/13, zero warnings. Source/vector/audit integrity
verified, six PDF pages checked; page 30 formula/figure errors persist. Evidence:
codex_helper/ingestion-1-vs-2-report.md and comparison JSON/text. Earlier provider/
A2/local-mode evidence remains in helper reports. Real Moodle unconfigured,
Word absent, no Git repository; frontend verified by AppTest, no browser visual QA.
