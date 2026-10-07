# Project Knowledge

## Purpose and current state

UPRAISE WP7 AI implements A1 ingestion, A2 material management, A3 draft lessons,
A4 status, A5 result/audio, A6 cancellation/receipt and A8 ready-lesson listing. Native V3 teacher/speech
applies explanation levels and source-label-based translation (lesson-v3-a3-8). Human approval remains
external. Finalized text/SSML persists in PostgreSQL; A5 JSON/audio is temporary.
The optional frontend now opens on Learning: select an available lesson, start,
read its text and play teacher voice; progress stays in the Streamlit session.

## Architecture and data flow

- FastAPI: /v1/documents (POST/GET/DELETE by ID), POST /v1/lesson-jobs,
  GET/DELETE /v1/jobs/{job_id}, GET .../result and .../audio/{chunk_id}, /health.
  MW owns lesson pace, learner state and human approval. AI also retains finalized
  DRAFT text/SSML in PostgreSQL; Qdrant stores
  source material only. Other original A/B/C paths remain reserved.
- A8 GET /v1/lesson-jobs requires jobs:read. Client/course/active-source/DONE/
  unexpired/no-cleanup filters precede pagination; returns DRAFT metadata only.
- learning.py uses A8/A5 only. One-part text/WAV playback/navigation rechecks A5;
  expiry/identity changes and local A2/A6 clear caches. No automatic A3/A6/TTL extension.
- INGRESS enforces OAuth/client/course/scopes/mTLS; no proxy headers. Explicit
  development + local_test_mode grants authoring only to real private/loopback peers.
- A1 commits bytes/job, returns202 document_id+job_id/A4 Location. A5 uses A3
  job_id. User removed hash/origin guard scans.
- Ingestion: Docling/OCR -> Nova candidates/source comparison -> quality-aware
  chunks -> Titan -> isolated Qdrant attempt/count -> audit -> DONE.
  Undeleted DONE + active_attempt is activation authority; vector tokens must match.
- A3 returns202 job_id, awaits pending A1, rejects unknown IDs, reads all active
  chunks in document/page/chunk order. Cross-window chunks are assigned once with
  all pages. Defaults:5page source windows/~200word output; no silent truncation.
- A3 uses copied V3 explain -> review_explanation -> finalize_explanation, with final
  citation/math checks and one editor revision. Reports retain pre-edit/source/speech
  findings; status stays DRAFT. No extra PDF judge or post-edit source review.
- A3 generates in stored A1 language; differing primary tags trigger finalized-
  prose translation/review before splitting, PG and target-language speech.
  Labels are declared, not detected. Formulas/citations/code/pages/order remain
  immutable. Checkpoints prevent repeated work; A4 shows TRANSLATE. Parser accepts
  one intact schema-valid object, never repairs strings. Reviewer masks notation,
  requires passage evidence and allows one correction/recheck (2-4 calls).
- Beginner/intermediate/advanced (default intermediate) controls all three role
  system prompts, including recovery; accuracy/coverage stay required. Reviewer flags
  level mismatches. A5 learner_level_applied=true, prompt explanation-level-v1.
  Math narration has no level policy; V3 MathJax/contextual/deterministic fallbacks remain.
- V3 PCM/WAV uses matching SSML/visemes. Overshoot is rescaled/flagged; malformed
  marks fail. Actual engines, including standard fallback, are recorded.
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

- FUTURE_UPDATES.md: Git-ignored local backlog; consult for future feature updates.
  Worker concurrency is the next proposed update, followed by DOCX/CSV and A7.
- README.md, compose.yaml, .env.example: setup. backend/docs/{a1,a2,a3,a5,a6,a8,
  local-testing}.md: maintained contracts; api-baseline.md preserves draft decisions.
- backend/src/upraise_ai/: api/, schemas/, guards/, main.py expose/wire APIs;
  core/config.py owns signatures, budgets, providers and OAuth/TLS.
- services/pdf/ and ingestion helpers: A1. services/lesson_*.py: A3/A5;
  services/v3/ retains original code, provenance README and SHA origin.json.
- storage/, workflows/, workers/: persistence, acceptance/control, supervised work.
- frontend/{app,api_client,learning}.py: API-only Learning and A1-A6 testbench;
  frontend/tests/: AppTest/fake HTTP, including player identity/expiry/navigation.
  backend/tests/test_a8_integration.py: scoped catalog/availability/pagination.
- backend/{pyproject.toml,uv.lock,Dockerfile,math_runtime/}: pinned build/math.
- callback_tls/README.md: ignored read-only secret mounts; credentials never logged/built.
- Legacy V3: ../Uprise/v2/backend/learning/v1/.
- backend/tests/: A3/V3/levels/translation/speech/archive, A5 audit/TTL/locks and
  original V3 regressions; AWS mocked.
- codex_helper/: ignored comparison reports/checkpoints and validation scripts/logs.
  validate-a3/a5.ps1 uses disposable services; a3-{text,level,translation}-*.log records QA.

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

- No backend learner state; frontend progress is session-only. Source/drafts/images
  are untrusted prompt data.
  Local auth never bypasses outbound callback security.
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
- Workload deadlines never skip source/reset on progress; max12h, default3 attempts.
  Budgets in config; Linux parent-death/group shutdown supervises writers.
- A1 artifact retention defaults off; A3 TTL is independent. Job/audit history
  survives. External artifact locks are never unlinked. A6 requires cleanup/audit
  before204; busy writers503 with recovery.
- Scopes: documents:read/write, lessons:write, jobs:read/write. Inaccessible IDs404;
  key/action conflicts409, malformed422. A1 response contains document_id and job_id.

## Known limitations and next work

- A1 false passes/rejections/ValidationExceptions: codex_helper/ingestion-1-vs-2-vs-3-report.md.
- A3 reports do not guarantee coherence or pedagogical suitability. Huge windows
  fail explicitly; reduce windows or tune request budgets.
- v5 Italian output was English; v6/v7 failed translation; v8 fixes parser/masked
  review/feedback. Job47194bc7 DONE first attempt:14 Italian-prose chunks, matching
  PG text/SSML/valid WAVs. Binomial/matrix, spoken-newline, English math-speech and
  page31 figure errors remain: codex_helper/italian-v8-assessment-47194bc7.md.
- Level/source defects: codex_helper/level-assessment/assessment.md. Beginner
  U+0008 invalidated SSML; original V3 returns no-audio warnings, A3 fails. See
  beginner-retry-diagnosis.md/original-speech-comparison.md in codex_helper/.
  AWS errors lose provider codes; no post-edit source review; MW callbacks unverified.
- Production gateway/Moodle, HA/backups, retention/audit erasure, orphan cleanup
  and cross-host artifact storage remain open; other APIs/learner RAG are reserved.
- Archive-read/erasure APIs remain undefined; inspect job status for partial archives.
  Deleted temporary text cannot be restored. See FUTURE_UPDATES.md for concurrency,
  researched DOCX/CSV parsing and deferred A7 regeneration.
- Learning excludes expired/acknowledged outputs; archive playback needs future
  design. DRAFT/source/speech defects remain. No learner Q&A/B APIs.
  Original v2 Learning V1 uses PG lectures/disk voice/SQLite through Python calls;
  comparison: codex_helper/frontend-learning-comparison.md.

## Recent decisions

- 2026-10-07: User requested simple frontend course learning and explicitly named
  its approved read-only ready-lesson list API A8. Existing A1-A6 contracts/TTL remain.

## User notes (preserve verbatim)

## Last verified

2026-10-07: A8 route/schema/repository/service, frontend app/learning/build exclusions,
their maintained tests/docs and Compose checked. 26 frontend checks passed;
backend full run:466 passed, one existing startup deadline test lacked child.pid,
then passed its isolated retry. All A8 checks passed. Changed-file Ruff/Compose/
backend+frontend builds passed. Idle backend/frontend redeployed; workers unchanged.
Live A8 lists5 lessons. Real AppTest opened job47194bc7, verified displayed text/
WAV hashes/format for first2 parts and Start/Next/Previous/Back with no exceptions,
GET only. UI root/health200. Browser inspection unavailable (CUA setup failure).
Checks: codex_helper/a8-learning-*.
Job47194bc7 expiry17:18:25UTC today; no new paid generation or lifecycle calls.

2026-10-06: translation/model/config/V3 and a3.md checked;442 backend plus50 focused
translation checks passed. Identity lesson-v3-a3-8-1658e1bb2509 verified.
Job47194bc7 A5/PG/checkpoints/all14 WAVs and page31 evidence inspected read-only;
prose/storage/delivery pass, math/content defects remain. Document79fd4c5e DONE/en.
