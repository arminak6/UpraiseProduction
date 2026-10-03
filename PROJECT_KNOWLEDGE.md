# Project Knowledge

## Purpose and current state

UPRAISE WP7 AI: A1 PDF ingestion, A2 list/delete, A4 monitoring and A6 job control.
Durable jobs, Docling/Nova/Titan, Qdrant and separate frontend testbench.
Local testing is enabled; authentication defaults remain production.
Supervised worker source awaits Docker deployment/verification (engine unavailable 2026-10-03).

## Architecture and data flow

- FastAPI preserves A authoring, B runtime and C operations groups. Routes:
  POST/GET /v1/documents, DELETE /v1/documents/{document_id},
  GET/DELETE /v1/jobs/{job_id}, GET /health. Liveness returns {"status":"alive"};
  other Word routes remain reserved, not stubs.
- Middleware owns Moodle source identity/permissions, lesson pace/storage and
  human approval. It uploads PDF bytes/metadata or supplies an HTTPS Moodle URL.
  source_ref is the Moodle file ID; language is the document source language.
- INGRESS validates OAuth JWT/client/course/scopes and trusted mTLS assertions;
  missing settings return 503, proxy headers disabled. Explicit development/local
  mode grants local-test-client authoring scopes to actual private/loopback peers;
  production + local mode refuses startup. Ignored .env enables local mode here.
- Acceptance validates PDF support/limits and stages bytes with fsync before job
  commit and 202 {document_id}. Location identifies A4; optional Idempotency-Key
  protects retries. Intentional new uploads get fresh document/job UUIDs.
- Worker claims compatible queued/expired jobs using row locks, leases and
  heartbeats. Service execution uses fresh spawned job processes; the supervisor
  enforces phase/total deadlines and stops revoked writers before bounded recovery.
  Docling/OCR -> Nova math/figure enrichment -> structure-aware chunks
  -> Titan embeddings -> Qdrant count verification -> audit -> DONE commit.
- PostgreSQL has upraise_jobs operational state and separate upraise_audit
  lifecycle records. Neither stores source text, lessons or learner checkpoints.
- A DONE job's active_attempt identifies usable Qdrant points via ingestion_token.
  Future retrieval must also exclude deletion_requested_at; raw points are not authority.
- A2 lists undeleted client/course jobs with status/version and active Qdrant
  counts. Deletion commits a marker, fences workers, removes all attempts by
  course/document filter, verifies zero, audits and completes before 204.
  Worker recovers interrupted deletion; repeated deletes do not duplicate audit.
- A6 reason=CANCEL|RECEIVED + jobs:write: CANCEL fences queued/running work;
  RECEIVED acknowledges DONE/FAILED and retains results/active vectors. Both
  purge temporary/inactive output, preserve source/history and audit before 204.
  File locks and durable requests coordinate cleanup/restart. A4 reports pending/receipt.
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
  artifact locking and deletion/job cleanup recovery. workers/{supervision,budgets}.py:
  process/group termination, nonblocking ownership checks and workload allowances.
- storage/{jobs,audit,files,vectors,migrate}.py: durable queue, minimal audit,
  staged sources/artifacts/locks, per-attempt vectors and repeatable bootstrap.
- backend/tests/: extraction, JWT/mTLS, real isolated DB/vector lifecycle and
  A2/A6 races/recovery. Tests clean only their own schemas, collections and audit.
- frontend/{app,api_client}.py: authoring/job forms, polling, HTTP testbench,
  same-origin status URLs, bearer/mTLS/private CA and safe errors; no backend/AWS/DB
  access. frontend/tests/ and README.md contain checks/setup.
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
- Timeout settings do not change pipeline signatures or skip content. Defaults:
  total attempt 12h; parse 180s + 30s/page with OCR (10s without); enrichment
  180s + 180s/region; embedding 120s + 90s/chunk. See a1.md for all settings.
  Budgets start at phase entry, not last progress. Timeout/crash retries are bounded.
  Child owns the artifact lock; Linux parent death and group shutdown stop writers.
  Ownership reads never block watchdog I/O (at most one daemon reader); one lease
  without verification stops work. Recovery is supervised for at most one lease.
- Prepared checkpoints avoid repeated provider calls on publication retry.
  Workers claim matching signatures; drain before pipeline changes or retain
  compatible workers. Reuse a submission key for network retries across deployment.
- Automatic artifact retention is disabled by default. Positive
  UPRAISE_ARTIFACT_RETENTION_DAYS removes terminal/deleted job files, not history.
  A6 explicitly purges temporary output while retaining original source.pdf.
  File locks live in data_dir/.locks and are never unlinked with working files.
- Scopes: A2 documents:read/write, A4 jobs:read, A6 jobs:write. Inaccessible/deleted
  IDs return 404; conflicts 409, malformed input 422. Same A6 action resumes cleanup.
  Deleted/cancelled A1 keys return 409 (JOB_CANCELLED for cancellation). A2 lists
  CANCELLED with zero active chunks; A6 preserves registry/history.
- A6 may return 503 JOB_CLEANUP_PENDING + Retry-After:2 until the supervised
  child stops. CANCELLED is committed immediately; publication stays fenced.
  No 204 precedes artifact cleanup and audit. Receipt keeps DONE/FAILED visible.
- Testbench credentials/results are session-only; TLS temporary files are deleted.
  Writes require submission; navigation never repeats them. Polling stops at
  terminal states/errors. Never forge gateway headers or put secrets in helpers.

## Known limitations and next work

- Moodle identity/gateway/signed URLs remain unverified. Local testing works.
- Single-host Compose; HA needs shared/object storage and replication. Retention,
  audit erasure/export, orphan reconciliation, backups and disk operations remain open.
- Future retrieval must enforce undeleted + DONE/active_attempt. Crashes can leave
  inactive points or INDEX_VERIFIED without DONE. Global audit policy beyond A1
  lifecycle/A2 delete/A6 actions is deferred, including A4 reads/INGRESS rejections.
- A6 currently operates on A1 ingestion jobs. Future lesson/audio jobs need their
  own output adapters. A synchronous external provider request already sent may
  finish or bill after the local child is stopped. Deadline defaults need tuning
  with representative large PDFs; they are not measured throughput limits.
- Other APIs, learner guards, RAG/lessons, speech, telemetry and operations are deferred.
- Inline-math detection can mistake prose accents for math. DONE confirms
  processing/publication, not extraction accuracy. Real source comparison found
  accepted formula/diagram errors with zero warnings and a merged cross-page
  element represented by only its first crop. See comparison evidence below.

## Recent decisions

- 2026-10-03: User approved supervised worker fix, with generous workload-aware
  budgets for large PDFs. No schema/signature change; no live deployment/provider calls.
- 2026-10-02: User approved PDF extraction/source language/Moodle source_ref,
  durable jobs/audit/A4/Streamlit/local mode; removed Ingestion Guard and A1 JSON
  job_id, retaining Location/internal jobs.
- 2026-10-02: User requested A2 list/delete and removal of visual count/size limits.
  v1.3 rerun improves coverage but retains content errors; originals enable reprocessing.
- 2026-10-02: A6 approved/deployed with distinct cancel/receipt, durable cleanup,
  source preservation, audit and testbench controls.

## User notes (preserve verbatim)

## Last verified

2026-10-03: Current config, workers/{ingestion,supervision,budgets}.py,
services/pdf/{parser,processor}.py, new workload/supervision tests, Compose,
examples and A1/A6/readme documentation verified. Host Python 3.10: 80 passed,
39 skipped (37 DB integrations unavailable; two POSIX-only checks). Ruff and
Compose config pass. Real spawned hang/next-job, locks, ownership revocation,
blocked status reader, timeout/retry limits and terminal-state preservation tested.
Production Python 3.12/Docling and Linux/PostgreSQL/Qdrant checks remain unrun:
Docker engine unavailable. Source changes not deployed. No credentials, existing
job data or providers touched. Git now exists. Details: codex_helper/worker-supervision-validation.md.

2026-10-02 historical evidence: deployed A6 passed backend 70/frontend 15 tests;
six jobs remained DONE, compared vectors 94/98. See a6-validation.md and
a6-deployment-check.json in codex_helper. The 55-page comparison checked six source
pages and found page-30 formula/diagram errors despite zero warnings; see
codex_helper/ingestion-1-vs-2-report.md. Real Moodle remains unconfigured, Word
absent; earlier frontend QA used AppTest rather than browser inspection.
