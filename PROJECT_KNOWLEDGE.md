# Project Knowledge

## Purpose and current state

UPRAISE WP7 AI implements A1 ingestion, A2 material management, A3 draft lessons,
A4 status, A5 result/audio and A6 cancellation/receipt. Native V3 teacher/speech
applies explanation levels and source-label-based translation (lesson-v3-a3-8). Human approval remains
external. Finalized text/SSML persists in PostgreSQL; A5 JSON/audio is temporary.

## Architecture and data flow

- FastAPI: /v1/documents (POST/GET/DELETE by ID), POST /v1/lesson-jobs,
  GET/DELETE /v1/jobs/{job_id}, GET .../result and .../audio/{chunk_id}, /health.
  MW owns lesson pace, learner state and human approval. AI also retains finalized
  DRAFT text/SSML in PostgreSQL; Qdrant stores
  source material only. Other original A/B/C paths remain reserved.
- INGRESS enforces OAuth/client/course/scopes/mTLS; no proxy headers. Explicit
  development + local_test_mode grants authoring only to real private/loopback peers.
- A1 fsyncs bytes/commits job, returns202 document_id+job_id/A4 Location.
  A1 job_id supports A4/A6; A5 uses A3 job_id. User removed hash/origin guard scans.
- Ingestion: Docling/OCR -> Nova candidates/source comparison -> quality-aware
  chunks -> Titan -> isolated Qdrant attempt/count -> audit -> DONE.
  Undeleted DONE + active_attempt is activation authority; vector tokens must match.
- A3 returns202 job_id, awaits pending A1, rejects unknown IDs, reads all active
  chunks in document/page/chunk order. Cross-window chunks are assigned once with
  all pages. Defaults:5page source windows/~200word output; no silent truncation.
- A3 uses copied V3 explain -> review_explanation -> finalize_explanation, with final
  citation/math checks and one editor revision. Reports retain pre-edit/source/speech
  findings; status stays DRAFT. No extra PDF judge or post-edit source review.
- A3 generates in each document's stored A1 language. Differing primary tags
  trigger finalized-prose translation plus independent language/fidelity/level
  verification before splitting, PostgreSQL and target-language speech. Matching
  tags skip translation. Formulas/citations/code and paragraph pages/order stay
  immutable. Source and translated checkpoints prevent repeated generation/TTS
  work on retries; A4 can show TRANSLATE. Versions record translation/source labels.
  Labels are declared, not detected; EU voice checks remain. Translation parser
  recovers one intact schema-valid JSON object from wrapper damage; never repairs
  strings/content. Ambiguous/truncated/damaged content still fails with specific logs.
  Translation review compares paired prose with identical opaque notation tokens;
  English inside immutable equations cannot be treated as untranslated prose.
  Negative checks require exact passage/paragraph evidence and one bounded
  correction plus independent recheck (2 calls normally, at most4 per attempt).
- Beginner/intermediate/advanced (default intermediate) controls all three role
  system prompts, including recovery; accuracy/coverage stay required. Reviewer flags
  level mismatches. A5 learner_level_applied=true, prompt explanation-level-v1.
  Math narration has no level policy; V3 MathJax/contextual/deterministic fallbacks remain.
- V3 PCM/WAV uses matching SSML/visemes. Overshoot is rescaled/flagged; malformed
  marks fail. Actual engines, including standard fallback, are recorded.
- Independent worker/lesson-worker/lesson-delivery processes use artifact locks,
  leases, fencing and supervised deadlines; provider calls are at least once.
  upraise_jobs holds jobs; upraise_audit holds content-free audits. Sources/crops/
  checkpoints/temporary lesson JSON/audio share the artifact volume.
- upraise_jobs.prepared_lesson_chunks stores finalized text before TTS and SSML/
  parts before synthesis, with order, provenance, pre-edit review and prompt/model
  versions. Valid leases/active sources fence writes; differing retry text fails.
- MW callbacks use pinned allowlisted HTTPS/mTLS/short-lived OAuth. MW stores A5
  then sends A6 RECEIVED; callback2xx is no receipt.
- A5 checks scope/client/course/source/hash, shared reader locks and mandatory
  content-free audit (outage blocks delivery). Writers/cleanup stay exclusive.
- TTL24h from completion; A5 denies expired output immediately. Independent
  delivery recovers cleanup/callbacks; RECEIVED/CANCEL preserves sources/vectors.
- Disposal/A2 retains prepared_lesson_chunks, including failed/cancelled speech.
  Operator SQL joins parent jobs for ownership/status; no archive API/audio DB/backfill.
- A2 fences ingestion, purges all vector attempts and audits before204. A1 A6
  preserves source/active vectors/history; lesson A6 removes JSON/audio/checkpoints.

## Important paths

- FUTURE_UPDATES.md: Git-ignored local backlog; consult for future feature updates.
  Worker concurrency is the next proposed update, followed by DOCX/CSV and A7.
- README.md, compose.yaml, .env.example: setup. backend/docs/{a1,a2,a3,a5,a6,
  local-testing}.md: maintained contracts; api-baseline.md preserves draft decisions.
- backend/src/upraise_ai/: api/authoring/, schemas/, guards/ and main.py expose/wire
  APIs; core/config.py owns provider identities, signatures, budgets and TLS/OAuth.
- services/pdf/, formats.py, quality/chunking/embeddings/moodle.py: ingestion.
  lesson_{models,compat,prompts,translation,pipeline,speech,callback}.py: A3/A5.
  services/v3/ retains original code, provenance README and SHA origin.json.
- storage/: jobs/lessons/audit/files/vectors/migrate. workflows/: acceptance/control.
  workers/: ingestion/lessons/lesson_delivery/supervision/budgets.
- frontend/{app,api_client}.py: API-only A1-A6 testbench; frontend/tests/: AppTest/fake HTTP.
- backend/{pyproject.toml,uv.lock,Dockerfile,math_runtime/}: pinned build/math.
- callback_tls/README.md: ignored read-only secret mounts; credentials never logged/built.
- Legacy V3: ../Uprise/v2/backend/learning/v1/.
- backend/tests/: test_a3* covers V3/levels/translation/lifecycle/speech/archive;
  test_a5_integration covers pull/audit/TTL; test_artifact_locks covers process locks.
  test_v3_original_* preserves original regressions; AWS mocked.
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
- Lesson identity is lesson-v3-a3-8; existing sources need no re-ingestion, and new
  generation uses a fresh idempotency key. Raw failed replies are opt-in via
  UPRAISE_LESSON_CAPTURE_MODEL_OUTPUTS, only development + local_test_mode; ordinary
  A6/TTL cleanup removes job diagnostics. Translation captures both candidate and
  verifier replies when enabled; default logs contain no generated text.
- Workload deadlines never skip source or reset on progress; max12h per attempt,
  A3 overhead300+1200/section or180/outputchunk. Detailed A1 budgets in config.
  Linux parent-death/group shutdown and DB ownership checks supervise writers;
  retry maximum defaults3.
- A1 artifact retention defaults off; A3 TTL is independent. Job/audit history
  survives. External artifact locks are never unlinked. A6 requires cleanup/audit
  before204; busy writers503 with recovery.
- Scopes: documents:read/write, lessons:write, jobs:read/write. Inaccessible IDs404;
  key/action conflicts409, malformed422. A1 response contains document_id and job_id.

## Known limitations and next work

- Live v1.4 source verification has false passes/rejections and14 unexplained
  ValidationExceptions. See codex_helper/ingestion-1-vs-2-vs-3-report.md.
- A3 reports do not guarantee coherence or pedagogical suitability. Huge windows
  fail explicitly; reduce windows or tune request budgets.
- v5 Italian job25748535 produced English (italian-assessment.md); v6/v7 failed
  wrappers/review. Approved replays reproduced wrapper and English-inside-equation
  false rejection; v8 fixes parsing/masked review and feedback. Historical failed
  replies unavailable. v8 job47194bc7 DONE first attempt:14 Italian-prose chunks,
  matching PG text/SSML and valid delivered WAVs. Binomial/matrix, spoken-newline,
  English math-speech and page31 figure errors remain. See codex_helper/
  italian-v8-assessment-47194bc7.md and translation-failure-*/.
- Level/source defects: codex_helper/level-assessment/assessment.md. Beginner retry
  saved14 texts/nine WAVs before U+0008 invalidated SSML (beginner-retry-diagnosis.md).
  Original V3 shares this but returns no-audio warnings; A3 fails
  (original-speech-comparison.md); V4/V5 not reproduced. AWS errors lose provider
  codes; no post-edit source review; MW callbacks unverified.
- Production gateway/Moodle, HA/backups, retention/audit erasure, orphan cleanup
  and cross-host artifact storage remain open; other APIs/learner RAG are reserved.
- Archive-read/erasure APIs remain undefined; inspect job status for partial archives.
  Deleted temporary text cannot be restored. See FUTURE_UPDATES.md for concurrency,
  researched DOCX/CSV parsing and deferred A7 regeneration.

## Recent decisions

- 2026-10-05: Copied original V3 teacher/speech into A3 adapters.
- 2026-10-06: User requested durable text/SSML, all-role level prompts and
  source-label-based translation; human approval stays external.

## User notes (preserve verbatim)

## Last verified

2026-10-07: frontend/Dockerfile/app.py and Compose tools profile checked. Started
frontend only after plain compose up skipped it; localhost8501 root/health and
frontend-to-backend health return200. Initial-page AppTest has no exceptions.

2026-10-06: lesson_translation/models/config, a3.md and translation tests checked.
442 full backend tests plus final50 focused translation tests passed; changed-file
Ruff/Compose/build passed. Legacy copied V3 has2 pre-existing import lint findings.
Runtime lesson-v3-a3-8-1658e1bb2509/source hash verified after idle app restart;
all six services healthy. Logs: codex_helper/a3-translation-review-{validation,build}.log.
Job47194bc7 A5/PG/checkpoints/all14 WAV routes and page31 render inspected read-only;
Italian prose/storage/delivery pass; math speech/content still need review. No new
model calls or code changes for this assessment. Document79fd4c5e remains DONE/en.
