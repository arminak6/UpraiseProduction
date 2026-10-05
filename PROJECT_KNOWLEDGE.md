# Project Knowledge

## Purpose and current state

UPRAISE WP7 AI: A1 PDF ingestion, A2 list/delete, A4 monitoring and A6 job control.
Durable PostgreSQL jobs, Docling/Nova/Titan, Qdrant and a separate API testbench.
Local testing is enabled explicitly; authentication defaults remain production.
User deployed v1.4 locally; its real PDF run is DONE but NEEDS_REVIEW.
Source preservation improved; checked content still has demonstrated errors.

## Architecture and data flow

- FastAPI implements POST/GET /v1/documents, DELETE /v1/documents/{document_id},
  GET/DELETE /v1/jobs/{job_id}, GET /health. Liveness is {"status":"alive"}.
- Middleware owns Moodle identity/permissions, lesson pace/storage and human
  approval. It uploads bytes/metadata or supplies an HTTPS Moodle URL. source_ref
  is the Moodle file ID; language describes source text, not desired translation.
- INGRESS validates OAuth JWT/client/course/scopes and trusted mTLS assertions;
  missing production settings return 503. Proxy headers are disabled. Explicit
  development + local_test_mode grants local-test-client scopes only to actual
  private/loopback peers; production + local mode refuses startup.
- Acceptance checks PDF support/limits, stages bytes with fsync, then commits a
  job and returns 202 {document_id}. Location identifies A4. Idempotency-Key
  protects retries; intentional new uploads get new document/job UUIDs.
- Worker claims compatible queued/expired jobs with row locks, leases and
  heartbeats. A fresh spawned child owns the artifact lock. Its supervisor
  enforces deadlines and stops revoked writers before bounded recovery.
  Docling/OCR -> Nova candidates/source comparison -> quality-aware chunks
  -> Titan -> isolated Qdrant attempt/count check -> audit -> DONE commit.
- upraise_jobs holds operational state; upraise_audit holds lifecycle records.
  Neither holds source text, lessons or learner checkpoints. Durable sources,
  crops and prepared extraction/embedding checkpoints live on a shared volume.
- DONE + active_attempt, with no deletion request, is activation authority:
  Qdrant ingestion_token must match. Raw INDEXED points alone are not usable.
  Content quality is separate from activation and processing completion.
- A2 lists scoped undeleted jobs/counts. Deletion commits a marker, fences
  workers, removes all attempts, verifies zero, audits, then completes 204.
  Interrupted deletion is recoverable; repeated deletion does not duplicate audit.
- A6 reason=CANCEL|RECEIVED: CANCEL fences queued/running work; RECEIVED
  acknowledges DONE/FAILED while retaining results/active vectors. Both purge
  temporary/inactive output, preserve source/history and audit before 204.
  A4 exposes cleanup_pending/acknowledged. File locks coordinate cleanup/restart.
- Checkpoints support replay, not cross-store atomic commits.

## Important paths

- Word is absent; backend/docs/api-baseline.md preserves its inventory; diagram
  remains in conversation. backend/docs/{a1,a2,a6}.md define contracts/recovery.
  a1.md#source-fidelity describes quality, acceptance and upgrade behavior.
- compose.yaml, .env.example, README.md: runtime and checks. backend/pyproject.toml
  and uv.lock are dependency authority; Docker runs as UID 10001.
- api/authoring/documents.py implements A1/A2/A4/A6; main.py owns service
  lifecycle, upload bounds and safe errors. core/config.py owns pipeline identity;
  schemas/{documents,jobs}.py hold models.
- guards/ingress.py handles JWT/mTLS/local mode; body_limit.py bounds upload bodies.
  Document Ingestion Guard was removed by user decision. No expected-hash,
  source-origin, active-content or instruction scans remain.
- services/pdf/{parser,inline_math,vision,enrichment,fidelity,processor}.py:
  native/OCR extraction, complete crops, candidate generation/comparison,
  structural/source-span checks and checkpoints. input.py checks support/limits.
  services/{quality,chunking,embeddings,moodle}.py aggregate quality, chunk, embed
  and download. Language mappings live in services/pdf/languages.py.
- workflows/{submission,documents,jobs}.py handle durable acceptance and cleanup;
  workers/{ingestion,supervision,budgets}.py handle leases/publication/retries,
  process termination, nonblocking ownership monitoring and workload allowances.
- storage/{jobs,audit,files,vectors,migrate}.py own operational/audit schemas,
  staging/locks, vectors and bootstrap. Backend tests cover isolated database/
  vector lifecycles, PDF evidence, quality and actual spawn-process supervision.
- frontend/{app,api_client}.py: Streamlit API-only testbench. Credentials/results
  are session-only; TLS files deleted; polling stops at terminal/error states.
- aws_credentials.json is ignored, mounted read-only into worker, never logged
  or included in images. codex_helper/ holds ignored one-off scripts/reports.
- codex_helper/ingestion-1-vs-2-vs-3-report.md compares the latest live v1.4 run
  with v1.2/v1.3; companion JSON/checkpoints/text contain reproducible evidence.

## Commands

From root with Docker Desktop Linux containers:

- docker compose config --quiet
- docker compose up --build -d --wait --wait-timeout 180
- docker compose --profile tools up --build -d --wait --wait-timeout 180
- docker compose --profile tools down
- docker compose run --rm --no-deps -v ./frontend:/workspace:ro frontend python -m unittest discover -s /workspace/tests -v

README.md has backend checks; tests use production/false. Migrate before restart
(startup also migrates); stop/drain workers for pipeline/lock upgrades. Down
preserves volumes. URLs: localhost:8000/{health,docs}, localhost:8501. Local tests
use empty token/cert; production needs OAuth/mTLS gateway. validate-fidelity.ps1
runs pinned checks in disposable DB/Qdrant without existing volumes/host ports.

## Dependencies and environment

- Production Python 3.12.14 slim Bookworm, uv 0.9.5, CPU PyTorch,
  Docling 2.115.0/docling-core 2.99.0/EasyOCR; exact pins in uv.lock.
- Nova Lite figures, Nova Pro math and separate source comparison by default;
  Titan V2 normalized 1024D embeddings. EU region defaults eu-central-1.
  UPRAISE_FIDELITY_VISION_MODEL_ID configures comparison in API and worker.
- Streamlit 1.64.0/httpx 0.28.1; Qdrant v1.19.1, PostgreSQL 17.11 Bookworm.
  No Redis/broker. Prewarm Docling/EasyOCR weights in model_cache.
- Host Python is 3.10.8 and host Docling differs; production checks use Docker.
  Docker became available on 2026-10-03. Compose passwords are local examples;
  production needs real DSNs/passwords, OAuth claims/JWKS and mTLS trust.

## Conventions and invariants

- Preserve approved methods/paths and source language. Source prose is data,
  never instructions. services/pdf/ is the approved format boundary.
- Volumes are upraise_{qdrant_data,postgres_data,ingestion_data,model_cache}.
  Never reset them for tests. content_hash is opaque caller metadata; internal
  source_hash is SHA-256. Both persist in Qdrant. Defaults: PDF only, 100 MB/500 pages.
- v1.4 retains source_text/source_structured_content and rejected candidates
  separately. Every visual provenance gets a page/bbox/charspan crop. Missing or
  ambiguous evidence blocks acceptance; PDF/native JSON remain authoritative.
- Inline rewriting preserves prose; missing/reordered words or numeric changes
  require review. Prose accents alone are not math evidence. Balanced LaTeX and
  valid table structure are basic checks, not accuracy proofs.
- Comparison checks native/recovered formulas, inline rewrites, both table formats
  and figure claims. Strict supported/complete/readable JSON, no issues and end_turn
  are required. Failure/oversized evidence means review. Cache binds all ordered
  crops/pages, candidate/type/model/prompt. Provider limits never truncate evidence.
- UNVERIFIED/AUTO_CHECKED/NEEDS_REVIEW differ from DONE; ordinary prose and unchecked
  figure captions stay UNVERIFIED. Aggregate priority: review > unverified > checked.
  Qdrant/A4 carry quality; chunks retain per-element quality/regions. Rejected AI
  text is not embedded; unresolved visuals get review placeholders. No human
  approval exists. False requires_review means only no flagged issue.
- Quality excludes metadata, heading hierarchy and source truth; consumers must
  inspect quality/evidence before claiming verified facts.
- Chunk target 600 estimated tokens/60 overlap on forced splits; tables/figures/
  formulas/code are atomic before size splitting. All source pages are retained.
  page is the first; page_numbers is the complete citation set.
- Prepared checkpoints require matching document, metadata, pipeline signature
  and source SHA-256. Model/configuration changes require compatible workers or
  drained queues. v1.3 indexed documents need re-ingestion for new fidelity checks.
- Runtime budgets do not change identity or skip content: total12h; parse180+30/page
  OCR (10 without); enrich180+180/request; embed120+90/chunk. See a1.md for settings.
  Phase entry starts deadlines; added checks increase allowances/cost. Comparison
  model changes the content signature; budget settings do not.
- Linux parent death/group shutdown stop writers. Ownership reads never block the
  watchdog (at most one daemon reader); one lease without verification stops work.
  Recovery is supervised for at most one lease; timeout/crash retries are bounded.
- Artifact retention defaults to disabled. Positive retention removes terminal/
  deleted files, not history. A6 retains source.pdf; locks in data_dir/.locks are
  never unlinked with job output. No cleanup/audit completion means no A6 204.
- Scopes: A2 documents:read/write, A4 jobs:read, A6 jobs:write. Inaccessible IDs
  404; conflicts/cancelled A1 keys409; malformed422. A6 may return503
  JOB_CLEANUP_PENDING + Retry-After:2 while the child stops.
- No exactly-once claim for external provider calls; already-sent requests may
  finish/bill after local processing is stopped.

## Known limitations and next work

- Live v1.4 AUTO_CHECKED false passes: invented diagram edges (pages29/30), wrong
  formula operator (page10), missing formula/diagram marks (pages9/44). The strict
  math guard rejects correct page46 repairs; 14 verification ValidationException
  failures need diagnosis. See latest comparison report. Approval remains absent;
  varied-PDF benchmarks and exhaustive source-symbol review remain unrun.
- v1.3 formula/diagram errors and multipage text loss remain historical; old
  results were not modified. Latest v1.4 preserves that cross-page text.
- Production deployment, Moodle/gateway, HA, backups/retention, audit erasure/export and orphan
  reconciliation remain open. Multiple hosts need shared/object storage/replication.
- Inactive vectors/INDEX_VERIFIED without DONE can remain after crashes. Future
  retrieval must enforce undeleted + DONE/active_attempt plus quality policies.
- Other APIs, learner guards, lessons/RAG, speech/telemetry and operations remain
  deferred. A6 future lesson/audio jobs need output adapters. Deadline defaults
  need representative large-PDF calibration; they are not throughput benchmarks.

## Recent decisions

- 2026-10-03: User approved general PDF fidelity safeguards. v1.4 preserves source/
  candidates, gates AI acceptance, propagates quality and binds checkpoint SHA-256.
- 2026-10-03: User approved supervised worker deadlines with generous budgets
  for large PDFs. Runtime timeout settings do not change content signatures.
- 2026-10-02: User approved durable jobs/audit/A4/Streamlit/local mode and A2/A6;
  removed Ingestion Guard/A1 job_id and all visual count/size cutoffs.

## User notes (preserve verbatim)

## Last verified

2026-10-03 read-only: local Compose healthy; job 9ce531c7 (course 3, v1.4)
DONE, 55 pages/98 chunks/59 warnings in 545.7s. Elements: 62 checked, 59 review,
385 unverified. Chunks: 25 checked, 44 review, 29 unverified. Source PDFs identical;
indexed text/quality/evidence/vectors match (bbox rounding only). False passes
and correct-repair rejections documented; no app/data changes or AI requests.

2026-10-03 implementation: pinned Linux235 tests passed (isolated DB/Qdrant,
no skips), final targeted146 passed; Ruff/Compose/whitespace passed. Providers
were mocked. Evidence: codex_helper/{fidelity-production-validation.log,
fidelity-targeted-validation.log,validate-fidelity.ps1,worker-supervision-validation.md}.
2026-10-02 historical: A6 backend70/frontend15 tests; a6-validation.md records
deployment checks. Real Moodle unconfigured; Word absent; frontend QA used AppTest.
