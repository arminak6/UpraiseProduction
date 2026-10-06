# Project Knowledge

## Purpose and current state

UPRAISE WP7 AI implements A1 ingestion, A2 material management, A3 draft lessons,
A4 status, A5 result/audio and A6 cancellation/receipt. Native V3 teacher/speech
now applies requested explanation levels (lesson-v3-a3-5). Human approval remains
external. Finalized text/SSML persists in PostgreSQL; A5 JSON/audio is temporary.

## Architecture and data flow

- FastAPI: /v1/documents (POST/GET/DELETE by ID), POST /v1/lesson-jobs,
  GET/DELETE /v1/jobs/{job_id}, GET .../result and .../audio/{chunk_id}, /health.
  MW owns lesson pace, learner state and human approval. AI also retains finalized
  DRAFT text/SSML in PostgreSQL; Qdrant stores
  source material only. Other original A/B/C paths remain reserved.
- INGRESS enforces OAuth JWT/client/course/scopes and trusted mTLS assertions;
  proxy headers are disabled. Explicit development + local_test_mode grants
  local-test-client authoring access only to actual private/loopback peers.
- A1 fsyncs bytes, commits a job and returns202/document_id with A4 Location.
  User removed Ingestion Guard/hash/origin scans; PDF/HTTPS support limits remain.
- Ingestion: Docling/OCR -> Nova candidates/source comparison -> quality-aware
  chunks -> Titan -> isolated Qdrant attempt/count -> audit -> DONE.
  Undeleted DONE + active_attempt is activation authority; vector tokens must match.
- A3 returns202 job_id, waits known pending A1 jobs, rejects unknown IDs (use A1),
  scrolls all active chunks and preserves requested document/page/chunk order.
  Cross-window chunks are assigned once with all source pages. Defaults:5page
  source windows, approximately200word output chunks; no silent source truncation.
- A3 uses copied services/v3/ explain -> review_explanation -> finalize_explanation.
  Draft citations reach review; final citation/math checks occur during finalization.
  One editor revision if rejected; no extra Nova PDF judge or post-edit model review.
  Reports retain pre-edit findings/source/speech flags; business status stays DRAFT.
- Requested language controls text/speech, with EU-region voice checks. Beginner/
  intermediate/advanced (default intermediate) controls explanation difficulty in
  all three role system prompts, including recovery. Accuracy/coverage stay required;
  reviewer flags level mismatches. New A5 reports learner_level_applied=true and
  learner_level_prompt=explanation-level-v1; old output stays unchanged. Math narration
  has no teaching-level policy; V3 MathJax/contextual/deterministic fallbacks remain.
- V3 PCM/WAV uses matching SSML/visemes. Overshoot is rescaled/flagged; malformed
  marks fail. Actual engines, including standard fallback, are recorded.
- Independent worker, lesson-worker and lesson-delivery services use killable
  spawned processes, artifact locks, leases, fencing and supervised deadlines.
  Provider calls are at least once. upraise_jobs holds operational ingestion_jobs/
  lesson_jobs; upraise_audit holds separate content-free audits. Sources, crops,
  checkpoints and temporary lesson JSON/audio are on the shared volume.
- upraise_jobs.prepared_lesson_chunks stores finalized text before TTS and SSML/
  parts before synthesis, with order, provenance, pre-edit review and prompt/model
  versions. Valid leases/active sources fence writes; differing retry text fails.
- MW callbacks use allowlisted pinned HTTPS, mTLS and short-lived OAuth tokens.
  Basic/POST/certificate client auth supported. Callback2xx is not receipt;
  MW stores A5 output then sends A6 RECEIVED. Secrets remain deployment-only.
- A5 uses jobs:read/client/course/source/hash checks and shared reader locks.
  Writes/cleanup stay exclusive. JSON/WAV access audits are content-free and
  mandatory; audit outages block delivery. no-store; pull never acknowledges A6.
- Output TTL defaults24h from completion. A5 denies expiry immediately; independent
  delivery recovers deletion/callback retries. RECEIVED/CANCEL purge lesson output
  only, preserving sources/vectors. Durable markers/audit IDs recover cleanup.
- RECEIVED/CANCEL/TTL retain prepared_lesson_chunks, including text saved before
  failed/cancelled speech. A5 still returns410 after disposal. Operator DB access
  joins parent lesson_jobs for client/course/status; no archive API/audio DB blobs.
  Old jobs are not backfilled. A2 does not erase saved lesson text/history.
- A2 fences ingestion, purges all vector attempts and audits before204. A1 A6
  preserves source/active vectors/history; lesson A6 removes JSON/audio/checkpoints.

## Important paths

- README.md, compose.yaml, .env.example: setup. backend/docs/{a1,a2,a3,a5,a6,
  local-testing}.md: maintained contracts; api-baseline.md preserves draft decisions.
- backend/src/upraise_ai/: api/authoring/{documents,lessons}.py and schemas/ expose
  APIs; main.py wires services. guards/{ingress,body_limit}.py controls access.
- core/config.py: provider identities, signatures, budgets and TLS/OAuth settings.
- services/pdf/, formats.py: extraction/fidelity. services/{quality,chunking,
  embeddings,moodle}.py: source quality/chunks, vectors and downloads.
- services/lesson_{models,compat,prompts,pipeline,speech,callback}.py: A3 generation,
  native V3 API conversion and delivery. services/v3/ contains the original
  teacher/models/prompts/Polly/math code, provenance README and SHA origin.json.
- storage/{jobs,lessons,audit,files,vectors,migrate}.py: operational schemas,
  content-free audit, fsync/locks, active-vector retrieval and startup migration.
- workflows/{submission,documents,jobs,lessons}.py: acceptance/control.
  workers/{ingestion,lessons,lesson_delivery,supervision,budgets}.py: execution.
- frontend/{app,api_client}.py: API-only A1-A6 testbench; frontend/tests/: AppTest/fake HTTP.
- backend/{pyproject.toml,uv.lock,Dockerfile,math_runtime/}: pinned build/math runtime.
- callback_tls/README.md: ignored read-only secret mounts; real callback
  credentials not supplied or logged/built.
- Legacy V3: ../Uprise/v2/backend/learning/v1/.
- backend/tests/test_a3*.py: V3/levels/lifecycle/speech/callbacks/durable text; test_a5_integration.py:
  pull/audit/concurrency/TTL, test_artifact_locks.py: Windows/Linux process locks.
  test_v3_original_*.py keeps original regressions; AWS mocked.
- codex_helper/: ignored comparison reports/checkpoints and validation scripts/logs.
  validate-a3/a5.ps1 uses disposable services; a3-{text,level}-*.log records QA.

## Commands

From root, Docker Desktop Linux containers:

- docker compose config --quiet
- docker compose build backend
- docker compose up -d --no-deps --wait --wait-timeout 180 backend worker lesson-worker lesson-delivery
- docker compose logs -f backend worker lesson-worker lesson-delivery
- Frontend tests: see README.md; optional Streamlit uses Compose profile tools.

Migration is additive. Drain old jobs before changing pipeline identity; never
reset live test volumes or re-ingest sources for A3. Local API8000/Streamlit8501.

## Dependencies and environment

- Production Python3.12.14 slim Bookworm/uv0.9.5; Docling2.115.0,
  docling-core2.99.0/EasyOCR/CPU Torch. Host Python3.10.8 differs: use pinned Docker.
  UID10001; exact dependencies in uv.lock.
- Nova Lite figures and Nova Pro math/fidelity for A1; normalized TitanV2 1024D.
  A3 GPT OSS120B generator/Qwen235B reviewer; explicit EU region (eu-central-1 default).
- Node22 image digest, MathJax4.1.3/SRE5.0.0-rc.4 npm lock. EN/IT/FR/DE local math
  smoke tested. PDF evidence renders with direct pypdfium2 dependency.
- Polly neural:16kHz mono16-bit WAV; same-SSML visemes offset by PCM duration.
  Request limits:2800 billed/5800 total SSML characters.
- Streamlit1.64.0/httpx0.28.1; PostgreSQL17.11/Qdrant1.19.1; no Redis.
  Deployment needs credentials, gateway claims, MW TLS/IdP and model/IAM access.

## Conventions and invariants

- No learner state. Source/drafts/feedback/images are untrusted prompt data.
  Local auth never bypasses outbound callback security.
- Volumes: upraise_{qdrant_data,postgres_data,ingestion_data,model_cache}.
  Source content_hash is opaque caller metadata; source_hash is actual SHA-256.
  PDF defaults100MB/500pages. Lesson UUIDs are job-specific; indexes zero-based;
  duration_seconds and viseme time_ms; provenance derives from source IDs.
- A1 retains originals/rejected candidates and provenance crops. Strict source
  comparison JSON/end_turn gates acceptance; rejected text is not embedded.
  Quality priority: review > unverified > checked. DONE is processing completion.
- A1 targets600tokens/60 forced overlap, preserves atomic structures; page_numbers
  retains all source pages while page is only the first.
- Source/section/audio checkpoints bind complete ordered request/source/pipeline
  identity. Result/audio hashes are verified. Corrupt/missing503, pending409, expired410.
- Lesson identity is lesson-v3-a3-5; existing sources need no re-ingestion, and new
  generation uses a fresh idempotency key. Raw failed replies are opt-in via
  UPRAISE_LESSON_CAPTURE_MODEL_OUTPUTS, only development + local_test_mode; ordinary
  A6/TTL cleanup removes job diagnostics. Default logs contain no generated text.
- Budgets never skip source/change identity. Ingestion max12h: parse180+30/pageOCR
  (10 without), enrich180+180/request, embed120+90/chunk. A3 max12h:
  overhead300+1200/section or180/outputchunk; tune for large PDFs.
- Progress never resets deadlines. Linux parent-death/group shutdown kills writers;
  bounded DB ownership readers supervise children. Retry maximum defaults3.
- A1 artifact retention defaults off; A3 TTL is independent. Job/audit history
  survives. External artifact locks are never unlinked. A6 requires cleanup/audit
  before204; busy writers503 with recovery.
- Scopes: documents:read/write, lessons:write, jobs:read/write. Inaccessible IDs404;
  key/action conflicts409, malformed422. A1 response contains only document_id.

## Known limitations and next work

- Live v1.4 source verification has false passes/rejections and14 unexplained
  ValidationExceptions. See codex_helper/ingestion-1-vs-2-vs-3-report.md.
- A3 reports do not guarantee coherence or pedagogical suitability. Huge windows
  fail explicitly; reduce windows or tune request budgets.
- Historical speech failure c6e896a6 lacks the rejected provider reply. Live
  job241c4bda completed with30 checked WAVs/visemes and259 findings; sampled final
  text had unsupported claims. Reports do not re-review edited text. Evidence:
  codex_helper/a3-completed-lesson-verification.json. MW callbacks remain unverified.
- Production gateway/Moodle, HA/backups, retention/audit erasure, orphan cleanup
  and cross-host artifact storage remain open; other APIs/learner RAG are reserved.
- Durable lesson-text retention/erasure and archive-read APIs remain undefined;
  inspect job status for partial/failed archives. Deleted temporary text cannot
  be recovered automatically. CSV/DOCX remain unsupported: researched native
  Docling DOCX and Polars/PyArrow CSV, with section/record provenance required.

## Recent decisions

- 2026-10-05: User requested copying the working V3 implementation and adapting
  it to unchanged A3 inputs/results after repeated compatibility failures.
- 2026-10-06: User requested PostgreSQL retention of final text used for speech,
  as in Uprise/v2. Added prepared_lesson_chunks; retain after output disposal.
- 2026-10-06: User requested explanation levels in all three role system prompts.
  Applies existing learner_level option; fresh jobs/keys use the new policy.

## User notes (preserve verbatim)

## Last verified

2026-10-06: Checked level policy/model adapter/pipeline/config, A3 tests/docs.
388 tests passed in disposable PostgreSQL/Qdrant with fake providers; Ruff,
Compose and whitespace passed. Built/restarted4 services with idle queues;
all6 healthy, running lesson-v3-a3-5-f88e33e6d2f7. Live efef122c archive preserves
12 chunks matching A5 text/SSML; prior level flag remains false. No paid generation
or backfill by the agent. Evidence: codex_helper/a3-level-{validation,build}.log,
a3-level-runtime-check.json; prior30-WAV checks: a5-runtime-check.json.
