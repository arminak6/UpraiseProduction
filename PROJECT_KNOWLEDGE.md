# Project Knowledge

## Purpose and current state

UPRAISE WP7 AI implements A1-A8, B1/B2/B4/B5 and C1/C2 with a Streamlit testbench.
PG retains text/SSML; A5 JSON/audio is temporary. Streamlit offers course playback.

## Architecture and data flow

- FastAPI exposes authoring, learning and operations APIs.
  MW owns pace/human approval; PG retains DRAFT text/SSML, Qdrant only sources.
  B3/B6 and C3 remain reserved.
- B1 POST /v1/sessions: sessions:write + exact lesson_job_id. Separate production
  JWT binds client/subject/context/hash/approval; local DRAFT preview. RAM consent,
  no capabilities. session_started audit precedes visibility; new201/retry200,
  preserved state/no expiry extension.
- B4 POST /v1/sessions/{id}/interrupts: request-local LangGraph classify/retrieve/
  respond, v2 teacher, exact PG context/pinned sources. Questions use paired document/
  attempt filters and source-language rewriting. No tracing/checkpoints. Grounding
  deferred (grounded=null, GROUNDING_NOT_VERIFIED); safety/language checks remain.
  Text/RAM WAV -> EU Transcribe; consent/JWT. Text/WAV/SSML/visemes or player control;
  TTS failure keeps text. Content-free audit; one turn/session, default4 concurrent,
  32MiB cache/100 messages, no renewal. Contract: backend/docs/b4.md.
- B2 POST /v1/sessions/{id}/chunk-end: lesson_id/chunk_id/cognitive_load_score
  (finite0..1), B1 version/client/course/JWT binding. Telemetry consent: >0.5 HIGH_COGNITIVE_LOAD,
  else PROCEED; no consent discards score. Latest score/index in RAM, fixed TTL;
  B5/expiry fences late commits. Audit IDs only; no AI/retrieval/speech. B6 future.
- B5 POST /v1/sessions/{id}/end: shared B1/B4 auth without questions consent;
  COMPLETED/STOPPED_BY_LEARNER/TIMEOUT/ERROR ->204. Clears RAM/B4 reply audio/counters,
  fences late delivery; idempotent content-free session_ended audit. Fixed TTL uses
  TIMEOUT cleanup; course WAVs/PG text/Qdrant keep independent retention.
- C1 /health is dependency-free; /ready checks core PG/Qdrant/artifacts (200/503)
  and AWS/worker degradation; absent collection allows initial A1. C2 /v1/status:
  jobs:read/course scope, nullable features/reasons/kill switch. No AWS polling;
  shared operational_observations: evidence15min/transient30s/heartbeat150s,
  isolated by model/region/credential hash; UNKNOWN intentional. Startup disables
  pause jobs without attempts; TTS-off keeps B4 text, blocks A3/A7. Playback/B5
  remain available; runtime C3 future.
- A8 GET /v1/lesson-jobs requires jobs:read. Client/course/active-source/DONE/
  unexpired/no-cleanup filters precede pagination; returns DRAFT metadata only.
- Learning uses A8/A5 identity/expiry checks. First Interrupt starts B1 then B4;
  Continue replays the part with player instructions, no position input. Finish/
  Back send B5, clear players/replies; failures retain retry metadata.
  B1/B4/B5 pages support JWT/consent, text/WAV and stable-ID retries. B2 has an
  explicit score-test page; it does not move Learning playback automatically.
  Health page tests C1/C2 explicitly; credential/course changes clear old results.
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
  one edit. Reports retain pre-edit/source/speech findings; no post-edit review.
- A7 POST /v1/audio:regenerate: lessons:write; lesson_job_id/chunk_id/teacher_review.
  Exact PG/pinned Qdrant -> three roles/inherited settings/A3 translation ->202.
  A4/A5/A8 expose complete DRAFT/revision; same chunk IDs/order, selected speech
  only. WAV links/copies survive parent cleanup; original/sessions preserved.
  Parent must be unexpired at acceptance. Checkpoints fence PG/TTS retries;
  POST duplicates create jobs (idempotency deferred). UI: Revise chunk (A7).
- A3 generates in declared A1 language; differing primary tags trigger translation/
  review before splitting/PG/target speech. Notation/citations/code/pages/order
  immutable; checkpoints avoid repeats, no parser repair; one evidenced recheck.
- Beginner/intermediate/advanced (default intermediate) controls all3 role prompts;
  reviewer flags mismatches. A5 learner_level_applied=true, explanation-level-v1.
- V3 PCM/WAV matches SSML/visemes; overshoot rescaled/flagged, malformed marks fail.
  Actual engines/fallbacks are recorded.
- Workers use locks/leases/fencing/deadlines; provider calls are at least once.
  upraise_jobs/upraise_audit retain jobs/content-free audit; artifacts share volume.
- prepared_lesson_chunks retains final text before TTS and SSML before synthesis,
  with order/provenance/review/model versions. Fenced retries cannot change text.
- MW uses pinned HTTPS/mTLS/OAuth; callback2xx is no receipt, A6 RECEIVED required.
  A5 checks ownership/source/hash/audit with shared locks; writers stay exclusive.
- TTL24h from completion; A5 denies expired output immediately. Independent
  delivery recovers cleanup/callbacks; RECEIVED/CANCEL preserves sources/vectors.
- Disposal/A2 retains prepared_lesson_chunks, including failed/cancelled speech;
  no archive API/audio DB/backfill.
- A2 fences ingestion, purges all vector attempts and audits before204. A1 A6
  preserves source/active vectors/history; lesson A6 removes JSON/audio/checkpoints.

## Important paths

- FUTURE_UPDATES.md: ignored workers/DOCX/CSV/A7/grounding/runtime/C3 backlog.
- README.md/compose.yaml/.env.example: setup; backend/docs/: contracts and decisions.
- backend/src/upraise_ai/: api/, schemas/, guards/, main.py expose/wire APIs;
  core/config.py owns signatures, budgets, providers and OAuth/TLS.
- api/runtime/, schemas/, workflows/{sessions,interrupts}.py, storage/sessions.py,
  guards/session_jwt.py, services/interrupt_{models,stt}.py: B1/B2/B4/B5 runtime.
  schemas/chunk_end.py, docs/b2.md, tests/test_b2*: B2; frontend/chunk_end.py: UI.
- {services,storage,api,schemas,guards}/operations*: C1/C2;
  backend/docs/c1-c2.md and tests/test_c_operations*.
- services/pdf/: A1; lesson_*.py: A3/A5; v3/: original code/provenance.
- schemas/revisions.py, workflows/revisions.py, services/revision_{models,pipeline}.py,
  backend/docs/a7.md, backend/tests/test_a7*: A7; frontend/chunk_revision.py: UI.
- frontend/: HTTP testbench/player; frontend/tests/: AppTest/fake HTTP.
  backend/tests/test_{a8,b1,b4,b5,c_operations}*: runtime/catalog/operations.
- backend/{pyproject.toml,uv.lock,Dockerfile,math_runtime/}: pinned build/math;
  callback_tls/README.md: secret mounts; AWS mounts in backend/workers.
- Legacy V3: ../Uprise/v2/backend/learning/v1/.
- backend/tests/: generation, runtime, speech, storage/locks; AWS mocked.
- codex_helper/: ignored helpers/logs and disposable validation scripts.

## Commands

From root, Docker Desktop Linux containers:

- docker compose config --quiet
- UI: docker compose --profile tools up --build -d --no-deps --wait frontend
  Tests: frontend/README.md; plain compose up skips UI.
- API/UI-only deploy: docker compose build backend frontend; then
  docker compose --profile tools up -d --no-deps --wait --wait-timeout 180 backend frontend
- C migration: docker compose run --rm --no-deps migrate; refresh API/UI plus
  worker lesson-worker lesson-delivery with the same up command/shared configuration.

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
- LangGraph1.2.14/amazon-transcribe0.6.4; STT needs transcribe:StartStreamTranscription.
  Streamlit1.64.0/httpx0.28.1; PostgreSQL17.11/Qdrant1.19.1; no Redis.
  Deployment needs credentials, gateway claims, MW TLS/IdP and model/IAM access.

## Conventions and invariants

- B1 RAM max10000, fixed1800s capped by lesson/JWT, sweep30s; one process, no renewal.
  Restart clears state/end records, including pending audit retries. B5 keeps bounded
  end metadata up to30min (retries204, no reopen); no learner DB/Redis. Sources untrusted.
- Volumes: upraise_{qdrant_data,postgres_data,ingestion_data,model_cache}.
  Source content_hash is opaque caller metadata; source_hash is actual SHA-256.
  PDF defaults100MB/500pages. Chunk identity is job_id+chunk_id (A7 keeps IDs);
  indexes zero-based;
  duration_seconds and viseme time_ms; provenance derives from source IDs.
- A1 retains originals/candidates/crops; strict JSON/end_turn comparison, priority
  review > unverified > checked. DONE means completion. 600tokens/60 overlap;
  atomic structures/all page_numbers retained (page is first only).
- Checkpoints bind request/source/pipeline: corrupt503, pending409, expired410.
- lesson-v3-a3-8 needs no re-ingestion; new generation uses fresh idempotency key.
  UPRAISE_LESSON_CAPTURE_MODEL_OUTPUTS is local-only, removed by A6/TTL; logs omit text.
- Deadlines never reset on progress; max12h/3 attempts; supervised Linux children.
- A1 retention defaults off, A3 independent; job/audit history survives. Artifact
  locks never unlinked; A6 cleanup/audit before204, busy writers503 with recovery.
- Scopes: documents:read/write, lessons:write, jobs:read/write, sessions:write.
  Inaccessible404; conflicts409; malformed422.

## Known limitations and next work

- A1/A3 defects: codex_helper/ingestion-1-vs-2-vs-3-report.md,
  italian-v8-assessment-47194bc7.md and level-assessment/assessment.md. Huge windows
  fail; reviews do not guarantee suitability. Beginner U+0008 invalidates SSML:
  original warns, A3 fails (beginner-retry-diagnosis.md/original-speech-comparison.md).
  Job errors lose provider details; MW callbacks unverified.
- Gateway/Moodle, HA/backups, retention/erasure, orphan cleanup and cross-host
  storage remain open. No archive-read API. Backlog: concurrency, DOCX/CSV, A7 idempotency.
- Learning excludes expired/acknowledged outputs; grounding deferred (backlog4).
  False gate: codex_helper/b4-rag-synthetic-diagnosis.py/.log. B4 synchronous,
  WAV max60s/6MB. AWS tests invented-only; earlier real-course egress auto-review rejected.
- v2 uses PG/disk voice/SQLite Python calls: codex_helper/frontend-learning-comparison.md.

## Recent decisions

- 2026-10-09: B2 approved: caller's cognitive_load_score, test threshold0.5;
  lesson/chunk validation and existing telemetry-consent rule retained.

## User notes (preserve verbatim)

## Last verified

2026-10-09: schemas/chunk_end.py, runtime/sessions.py, storage/{sessions,audit}.py,
frontend/{chunk_end,session_end,app}.py and docs/b2.md checked. Runtime130 and
isolated integration31 passed (fake AWS); frontend62 plus final focused5 passed.
Changed-file Ruff/Compose/diff/build passed. API/UI refreshed; all7 healthy.
Live B2 verified both decisions, exact0.5, consent, B5 and IDs-only audit; no AWS
calls. Job51e8c520's12 PG chunks/result/WAV hashes preserved;10 DONE/9 FAILED,
none active. Logs: codex_helper/b2-*. No migration. B4 power-grid18.7/12.4/page4
verified in Qdrant/PDF (rag-source-page4.png). PowerShell5.1 needs UTF8 reads.
