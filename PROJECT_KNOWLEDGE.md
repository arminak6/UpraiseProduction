# Project Knowledge

## Purpose and current state

UPRAISE WP7 AI implements A1-A6, A8 listing, B1 session start and B4 interruptions.
V3 generation applies levels and source-label translation (lesson-v3-a3-8).
Human approval is external. Finalized text/SSML persists in PostgreSQL;
A5 JSON/audio is temporary. Streamlit Learning offers course selection,
text/voice and session-only progress.

## Architecture and data flow

- FastAPI exposes documents, lesson-jobs, job/result/audio, sessions and /health.
  MW owns pace/human approval; PG retains DRAFT text/SSML, Qdrant only sources.
  B2/B3/B5/B6 and other original A/C paths remain reserved.
- B1 POST /v1/sessions: sessions:write + exact lesson_job_id. Separate production
  JWT binds client/subject/context/hash/approval; local DRAFT preview is allowed.
  Consent stays in RAM; B1 omits capabilities.
  session_started audit precedes visibility. New201; unchanged retry200 preserves
  state and never extends expiry.
- B4 POST /v1/sessions/{id}/interrupts: request-local LangGraph classify/retrieve/
  respond; no checkpoints/tracing. Exact PG teaching text + current Qdrant evidence;
  questions always search paired document/attempt filters with pinned versions.
  Native v2 teacher.respond/source/dialogue formatting + exact PG context. Support
  returns the teacher turn directly (grounded=null, contextual source references).
  Course questions retain reviewer/quote/claim/page checks; source-language queries.
  JSON text or RAM-only WAV -> EU Transcribe; questions consent/JWT required.
  Inline WAV/SSML/visemes or player instruction; TTS failure retains verified text.
  Backend needs the read-only AWS credential mount, alongside A1/A3 workers.
  Content-free audit; one turn/session, default4 concurrent, bounded history,
  32MiB response cache, max100 messages/session; expiry never renewed.
- A8 GET /v1/lesson-jobs requires jobs:read. Client/course/active-source/DONE/
  unexpired/no-cleanup filters precede pagination; returns DRAFT metadata only.
- Frontend Learning uses A8/A5 with expiry/identity checks and cache invalidation.
  B1 page offers explicit start/retry, local consent and signed JWT. B4 page loads
  A5 chunks, sends text/WAV and retries stable IDs. Learning's Interrupt button
  removes lesson audio; first explicit message starts B1 with consent/JWT then
  B4 on the current chunk. Continue rechecks A5 and applies replay/speed instructions.
  No position input; Continue replays the current part. Sessions survive navigation,
  not expiry.
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
- A3 V3 explain -> review_explanation -> finalize_explanation: citation/math checks,
  one editor revision. Reports retain pre-edit/source/speech findings; DRAFT stays.
  No extra PDF judge or post-edit source review.
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
- README.md, compose.yaml, .env.example: setup. backend/docs/{a1,a2,a3,a5,a6,a8,b1,b4,
  local-testing}.md: maintained contracts; api-baseline.md preserves draft decisions.
- backend/src/upraise_ai/: api/, schemas/, guards/, main.py expose/wire APIs;
  core/config.py owns signatures, budgets, providers and OAuth/TLS.
- api/runtime/, schemas/, workflows/{sessions,interrupts}.py, storage/sessions.py,
  guards/session_jwt.py, services/interrupt_{models,stt}.py: B1/B4 runtime.
- services/pdf/ and ingestion helpers: A1. services/lesson_*.py: A3/A5;
  services/v3/ retains original code, provenance README and SHA origin.json.
- storage/, workflows/, workers/: persistence, acceptance/control, supervised work.
- frontend/{app,api_client,learning,learning_interrupt,session_start,learner_interrupt}.py: UI;
  frontend/tests/: AppTest/fake HTTP. backend/tests/test_{a8,b1,b4}*: runtime/catalog.
- backend/{pyproject.toml,uv.lock,Dockerfile,math_runtime/}: pinned build/math.
- callback_tls/README.md: ignored read-only secret mounts; AWS file mounts in backend/workers.
- Legacy V3: ../Uprise/v2/backend/learning/v1/.
- backend/tests/: generation, translation, speech, archive, audit/TTL/locks; AWS mocked.
- codex_helper/: ignored reports/logs; validate-{a3,a5,b1,b4}.ps1 uses disposable services.

## Commands

From root, Docker Desktop Linux containers:

- docker compose config --quiet
- UI: docker compose --profile tools up --build -d --no-deps --wait frontend
  Tests: frontend/README.md; plain compose up skips UI.
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
- LangGraph1.2.14/amazon-transcribe0.6.4; audio STT needs transcribe:StartStreamTranscription.
  Streamlit1.64.0/httpx0.28.1; PostgreSQL17.11/Qdrant1.19.1; no Redis.
  Deployment needs credentials, gateway claims, MW TLS/IdP and model/IAM access.

## Conventions and invariants

- B1 RAM dictionary: max10000 sessions, TTL1800s capped by lesson/JWT, sweep30s.
  Restart clears state; one backend process, no Redis/DB or activity renewal.
  Frontend progress is session-only. Source/drafts/images are untrusted;
  local auth preserves callback security.
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
- Scopes: documents:read/write, lessons:write, jobs:read/write, sessions:write.
  Inaccessible404; conflicts409; malformed422.

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
- Production gateway/Moodle, HA/backups, retention/erasure, orphan cleanup and
  cross-host storage remain open. No archive-read API; status identifies partial
  archives. FUTURE_UPDATES.md covers concurrency, DOCX/CSV and A7.
- Learning excludes expired/acknowledged outputs. DRAFT/source/speech defects remain.
  B4 evidence checks do not certify source correctness. Synchronous inline JSON,
  no 2-second/streaming guarantee; WAV max60s/6MB. Real Bedrock/Polly works on
  invented Italian material. User's real Italian support test returned complete
  text/WAV; sources still require review. Agent inspected saved files locally;
  earlier automatic review rejected agent-initiated real-course content egress.
  Original v2 Learning V1 uses PG lectures/disk voice/SQLite through Python calls;
  comparison: codex_helper/frontend-learning-comparison.md.

## Recent decisions

- 2026-10-07: User approved A8/B1/B4 and reaffirmed original v2 teaching support.
  Current 24h playback, external approval and RAM-only learner content remain.

## User notes (preserve verbatim)

## Last verified

2026-10-07: Compared original v2 graph/teacher/frontend speech. Restored native
teacher.respond for support; independent verification remains for factual RAG
questions.35 B4 tests passed on disposable PG/Qdrant; Ruff/Compose/diff passed.
Deployed backend passed real EU Bedrock/Polly with invented Italian material:
exact response text fed speech, valid341804-byte WAV/134 visemes, no warnings.
No real project course/learner content sent by agent. Backend restart clears B1.
Logs/helpers: codex_helper/b4-v2-*. Prior49 frontend/567 backend tests passed.
User's saved b4-reply.json/WAV checked locally: complete1249-character Italian
answer, valid84.056s mono16kHz WAV matching JSON, SSML/1052 visemes consistent.
SOURCE_REQUIRES_REVIEW and grounded=null; support is not independently verified.
Windows PowerShell5.1 needs Get-Content -Raw -Encoding UTF8 for correct accents.
