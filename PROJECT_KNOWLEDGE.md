# Project Knowledge

## Purpose and current state

UPRAISE WP7 AI implements A1-A6, A8 ready-lesson listing and B1 lesson start.
V3 generation applies levels and source-label translation (lesson-v3-a3-8).
Human approval is external. Finalized text/SSML persists in PostgreSQL;
A5 JSON/audio is temporary. Streamlit Learning offers course selection,
text/voice and session-only progress.

## Architecture and data flow

- FastAPI: /v1/documents (POST/GET/DELETE by ID), POST /v1/lesson-jobs,
  GET/DELETE /v1/jobs/{job_id}, GET .../result and .../audio/{chunk_id}, /health.
  MW owns lesson pace and human approval; B1 opens ephemeral learner runtime state. AI retains finalized
  DRAFT text/SSML in PostgreSQL; Qdrant stores
  source material only. B2-B6 and other original A/C paths remain reserved.
- B1 POST /v1/sessions: sessions:write + exact lesson_job_id. Separate production
  JWT binds client/subject/context/hash/approval; local DRAFT preview is allowed.
  Consent stays in RAM; B1 omits capabilities. B2-B6 remain reserved.
  session_started audit precedes visibility. New201; unchanged retry200 preserves
  state and never extends expiry.
- A8 GET /v1/lesson-jobs requires jobs:read. Client/course/active-source/DONE/
  unexpired/no-cleanup filters precede pagination; returns DRAFT metadata only.
- learning.py uses A8/A5; session_start.py adds an A8/B1 test page with explicit
  start/retry, manual context, local consent and masked signed-JWT mode.
  One-part text/WAV playback/navigation rechecks A5;
  expiry/identity changes and local A2/A6 clear caches. No automatic A3/A6/TTL extension.
- INGRESS enforces OAuth/client/course/scopes/mTLS; no proxy headers. Explicit
  development + local_test_mode grants authoring/B1 preview to private/loopback peers.
- A1 commits bytes/job, returns202 document_id+job_id/A4 Location. A5 uses A3
  job_id. User removed hash/origin guard scans.
- Ingestion: Docling/OCR -> Nova candidates/source comparison -> quality-aware
  chunks -> Titan -> isolated Qdrant attempt/count -> audit -> DONE.
  Undeleted DONE + active_attempt is activation authority; vector tokens must match.
- A3 returns202 job_id, awaits A1 and reads active chunks in source order.
  Cross-window chunks are assigned once with all pages; defaults5pages/~200words,
  no silent truncation and unknown IDs rejected.
- A3 uses copied V3 explain -> review_explanation -> finalize_explanation, with final
  citation/math checks and one editor revision. Reports retain pre-edit/source/speech
  findings; status stays DRAFT. No extra PDF judge or post-edit source review.
- A3 generates in declared A1 language; differing primary tags trigger translation/
  review before splitting/PG/target speech (A4 TRANSLATE). Notation/citations/code/
  pages/order remain immutable. Checkpoints avoid repeats; parser never repairs
  strings. Masked reviewer needs passage evidence; one correction/recheck.
- Beginner/intermediate/advanced (default intermediate) controls all3 role system
  prompts/recovery; reviewer flags mismatches, accuracy/coverage remain required.
  A5 learner_level_applied=true, explanation-level-v1. Math narration keeps V3 fallbacks.
- V3 PCM/WAV matches SSML/visemes; overshoot rescaled/flagged, malformed marks fail.
  Actual engines/fallbacks are recorded.
- Independent workers use locks, leases/fencing/deadlines; provider calls are at
  least once. upraise_jobs holds jobs; upraise_audit holds content-free audits.
  Sources/crops/checkpoints/temporary lesson outputs share the artifact volume.
- upraise_jobs.prepared_lesson_chunks stores finalized text before TTS and SSML/
  parts before synthesis, with order, provenance, pre-edit review and prompt/model
  versions. Valid leases/active sources fence writes; differing retry text fails.
- MW callbacks use pinned allowlisted HTTPS/mTLS/short-lived OAuth. MW stores A5
  then sends A6 RECEIVED; callback2xx is no receipt.
- A5 checks scope/client/course/source/hash, shared reader locks and mandatory
  content-free audit (outage blocks delivery). Writers/cleanup stay exclusive.
- TTL24h from completion; A5 denies expired output immediately. Independent
  delivery recovers cleanup/callbacks; RECEIVED/CANCEL preserves sources/vectors.
- Disposal/A2 retains prepared_lesson_chunks, including failed/cancelled speech;
  no archive API/audio DB/backfill.
- A2 fences ingestion, purges all vector attempts and audits before204. A1 A6
  preserves source/active vectors/history; lesson A6 removes JSON/audio/checkpoints.

## Important paths

- FUTURE_UPDATES.md: ignored backlog: worker concurrency, DOCX/CSV, A7, runtime scaling.
- README.md, compose.yaml, .env.example: setup. backend/docs/{a1,a2,a3,a5,a6,a8,b1,
  local-testing}.md: maintained contracts; api-baseline.md preserves draft decisions.
- backend/src/upraise_ai/: api/, schemas/, guards/, main.py expose/wire APIs;
  core/config.py owns signatures, budgets, providers and OAuth/TLS.
- api/runtime/sessions.py, schemas/sessions.py, guards/session_jwt.py,
  storage/sessions.py, workflows/sessions.py and tests/test_b1*.py: B1 contract/state.
- services/pdf/ and ingestion helpers: A1. services/lesson_*.py: A3/A5;
  services/v3/ retains original code, provenance README and SHA origin.json.
- storage/, workflows/, workers/: persistence, acceptance/control, supervised work.
- frontend/{app,api_client,learning,session_start}.py: Learning and A1-A6/B1 testbench;
  frontend/tests/: AppTest/fake HTTP, including player identity/expiry/navigation.
  backend/tests/test_a8_integration.py: scoped catalog/availability/pagination.
- backend/{pyproject.toml,uv.lock,Dockerfile,math_runtime/}: pinned build/math.
- callback_tls/README.md: ignored read-only secret mounts; credentials never logged/built.
- Legacy V3: ../Uprise/v2/backend/learning/v1/.
- backend/tests/: generation, translation, speech, archive, audit/TTL/locks; AWS mocked.
- codex_helper/: ignored reports/validation logs; validate-a3/a5/b1.ps1 uses disposable services.

## Commands

From root, Docker Desktop Linux containers:

- docker compose config --quiet
- docker compose build backend
- docker compose up -d --no-deps --wait --wait-timeout 180 backend worker lesson-worker lesson-delivery
- docker compose logs -f backend worker lesson-worker lesson-delivery
- Start only UI: docker compose --profile tools up --build -d --no-deps --wait frontend
  Tests: frontend/README.md. Plain compose up skips the tools-profile UI.
- API/UI-only deploy: docker compose build backend frontend; then
  docker compose --profile tools up -d --no-deps --wait --wait-timeout 180 backend frontend

Migration is additive. Drain old jobs before changing pipeline identity; never
reset live test volumes or re-ingest sources for A3. Local API8000/Streamlit8501.

## Dependencies and environment

- Python3.12.14 Bookworm/uv0.9.5/UID10001; pinned Docker (host3.10 differs).
  Docling2.115.0/core2.99.0/EasyOCR/CPU Torch; dependencies in uv.lock.
- Nova Lite figures and Nova Pro math/fidelity for A1; normalized TitanV2 1024D.
  A3 GPT OSS120B generator/Qwen235B reviewer; explicit EU region (eu-central-1 default).
- Node22/MathJax4.1.3/SRE5.0.0-rc.4 npm lock; EN/IT/FR/DE math smoke tested.
  PDF evidence renders with pypdfium2.
- Polly neural:16kHz mono16-bit WAV; same-SSML visemes offset by PCM duration.
  Request limits:2800 billed/5800 total SSML characters.
- Streamlit1.64.0/httpx0.28.1; PostgreSQL17.11/Qdrant1.19.1; no Redis.
  Deployment needs credentials, gateway claims, MW TLS/IdP and model/IAM access.

## Conventions and invariants

- B1 uses a bounded thread-safe dictionary (default10000 sessions); TTL1800s capped
  by lesson/JWT expiry, swept every30s. Restart clears state; no session DB/Redis.
  Keep one backend process; shared state and activity renewal are future work.
  Frontend player progress is session-only; B1 is tested on its separate page.
  Source/drafts/images are untrusted. Local auth preserves callback security.
- Volumes: upraise_{qdrant_data,postgres_data,ingestion_data,model_cache}.
  Source content_hash is opaque caller metadata; source_hash is actual SHA-256.
  PDF defaults100MB/500pages. Lesson UUIDs are job-specific; indexes zero-based;
  duration_seconds and viseme time_ms; provenance derives from source IDs.
- A1 retains originals/rejected candidates/crops; strict comparison JSON/end_turn
  gates acceptance. Quality priority: review > unverified > checked. DONE is completion.
- A1 targets600tokens/60 forced overlap, preserves atomic structures; page_numbers
  retains all source pages while page is only the first.
- Checkpoints bind ordered request/source/pipeline. Corrupt/missing503, pending409, expired410.
- Lesson identity lesson-v3-a3-8; no source re-ingestion required. New generation
  uses a fresh idempotency key. UPRAISE_LESSON_CAPTURE_MODEL_OUTPUTS captures
  candidate/verifier replies only in development/local_test_mode; A6/TTL removes
  diagnostics. Default logs contain no generated text.
- Deadlines never skip source/reset on progress; max12h/default3 attempts.
  Config owns budgets; Linux parent-death/group shutdown supervises writers.
- A1 artifact retention defaults off; A3 TTL is independent. Job/audit history
  survives. External artifact locks are never unlinked. A6 requires cleanup/audit
  before204; busy writers503 with recovery.
- Scopes: documents:read/write, lessons:write, jobs:read/write, sessions:write. Inaccessible IDs404;
  key/action conflicts409, malformed422. A1 response contains document_id and job_id.

## Known limitations and next work

- A1 false passes/rejections/ValidationExceptions: codex_helper/ingestion-1-vs-2-vs-3-report.md.
- A3 reports do not guarantee coherence or pedagogical suitability. Huge windows
  fail explicitly; reduce windows or tune request budgets.
- v8 fixes translation parser/masked review/feedback. Job47194bc7 has14 Italian
  chunks, matching PG/SSML/WAVs. Binomial/matrix, spoken-newline, English math-speech
  and page31 figure errors remain: codex_helper/italian-v8-assessment-47194bc7.md.
- Level/source defects: codex_helper/level-assessment/assessment.md. Beginner
  U+0008 invalidated SSML; original V3 returns no-audio warnings, A3 fails. See
  beginner-retry-diagnosis.md/original-speech-comparison.md in codex_helper/.
  AWS errors lose provider codes; no post-edit source review; MW callbacks unverified.
- Production gateway/Moodle, HA/backups, retention/audit erasure, orphan cleanup
  and cross-host artifact storage remain open; other APIs/learner RAG are reserved.
- Archive-read/erasure APIs remain undefined; job status identifies partial archives.
  FUTURE_UPDATES.md covers concurrency, DOCX/CSV and A7.
- Learning excludes expired/acknowledged outputs; archive playback needs future
  design. DRAFT/source/speech defects remain. B1 only; learner Q&A/B2-B6 are reserved.
  Original v2 Learning V1 uses PG lectures/disk voice/SQLite through Python calls;
  comparison: codex_helper/frontend-learning-comparison.md.

## Recent decisions

- 2026-10-07: User requested simple frontend course learning and explicitly named
  its approved read-only ready-lesson list API A8. Existing A1-A6 contracts/TTL remain.
- 2026-10-07: User approved B1 with process-local memory and current 24h playback.
  No Redis or permanent audio/archive playback added. See docs/b1.md for JWT claims.

## User notes (preserve verbatim)

## Last verified

2026-10-07: Removed B1 capabilities from schema/workflow/UI; updated contract/docs.
68 B1 backend and35 frontend checks passed, plus Ruff/Compose/diff checks.
Backend/frontend rebuilt/redeployed. Live API and Streamlit AppTest verified Italian
new201/retry200, no capabilities in output/OpenAPI/display, AI notice and one audit;
navigation did not resubmit. Logs: codex_helper/b1-capabilities-*. Consent tests pass.
Prior backend full534 and A8 text/WAV checks passed. No paid model/TTS calls;
CUA setup unavailable. Italian job47194bc7 expires17:18:25UTC today; source79fd4c5e
DONE/en, generation identity lesson-v3-a3-8-1658e1bb2509 unchanged.
