# Project Knowledge

## Purpose and current state

UPRAISE WP7 AI implements A1-A6/A8, B1/B4/B5 and C1/C2 with a Streamlit testbench.
V3 supports levels/translation (lesson-v3-a3-8); human approval is external.
PG retains text/SSML; A5 JSON/audio is temporary. Streamlit offers course playback.

## Architecture and data flow

- FastAPI exposes authoring, learning and operations APIs.
  MW owns pace/human approval; PG retains DRAFT text/SSML, Qdrant only sources.
  A7, B2/B3/B6 and C3 remain reserved.
- B1 POST /v1/sessions: sessions:write + exact lesson_job_id. Separate production
  JWT binds client/subject/context/hash/approval; local DRAFT preview is allowed.
  Consent stays in RAM; B1 omits capabilities.
  session_started audit precedes visibility. New201; unchanged retry200 preserves
  state and never extends expiry.
- B4 POST /v1/sessions/{id}/interrupts: request-local LangGraph classify/retrieve/
  respond, native v2 teacher, exact PG context and pinned Qdrant sources. Questions
  search paired document/attempt filters with source-language rewriting. No tracing/
  checkpoints. Factual grounding deferred (grounded=null, GROUNDING_NOT_VERIFIED);
  strict safety/session-language checks remain. Text or RAM WAV -> EU Transcribe;
  consent/JWT required. Text/WAV/SSML/visemes or player instruction; TTS failure
  retains checked text. Content-free audit, one turn/session, default4 concurrent,
  32MiB reply cache/max100 messages; no expiry renewal. Contract: backend/docs/b4.md.
- B5 POST /v1/sessions/{id}/end: shared B1/B4 auth without questions consent;
  reason COMPLETED/STOPPED_BY_LEARNER/TIMEOUT/ERROR, empty204. Clears session RAM,
  B4 reply audio/cache and counters; fences late B4 delivery. Content-free
  session_ended audit is idempotent; fixed TTL uses the same TIMEOUT cleanup.
  A3 WAVs, PG teaching text and Qdrant sources remain under their own retention.
- C1 /health is dependency-free; /ready checks core PG/Qdrant/artifacts (200/503)
  and AWS/worker degradation. Missing collection allows initial A1. C2 /v1/status:
  INGRESS/jobs:read, authorized optional course_id, nullable features/reasons/kill switch.
  No AWS polling calls. SDK outcomes/worker heartbeat share operational_observations;
  provider evidence15min, transient failures30s, heartbeat150s. Model/region/credential
  hash isolates outcomes, including old live clients. UNKNOWN is intentional.
  Shared startup controls enforce global/course disables, pause jobs without attempts;
  TTS disable keeps B4 text, A3 needs TTS. Playback/management/B5 stay; C3 is future.
- A8 GET /v1/lesson-jobs requires jobs:read. Client/course/active-source/DONE/
  unexpired/no-cleanup filters precede pagination; returns DRAFT metadata only.
- Learning uses A8/A5 identity/expiry checks. First Interrupt starts B1 then B4;
  Continue replays the part with player instructions, no position input. Finish/
  Back send B5, clear players/replies; failures retain retry metadata. B1/B4/B5
  test pages support JWT/consent, text/WAV and stable-ID retries.
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
  guards/session_jwt.py, services/interrupt_{models,stt}.py: B1/B4/B5 runtime.
- {services,storage,api,schemas,guards}/operations*: C1/C2;
  backend/docs/c1-c2.md and tests/test_c_operations*.
- services/pdf/: A1; lesson_*.py: A3/A5; v3/: original code/provenance.
- storage/, workflows/, workers/: persistence, acceptance/control, supervised work.
- frontend/: HTTP testbench/player; frontend/tests/: AppTest/fake HTTP.
  backend/tests/test_{a8,b1,b4,b5,c_operations}*: runtime/catalog/operations.
- backend/{pyproject.toml,uv.lock,Dockerfile,math_runtime/}: pinned build/math;
  callback_tls/README.md: secret mounts; AWS mounts in backend/workers.
- Legacy V3: ../Uprise/v2/backend/learning/v1/.
- backend/tests/: generation, runtime, speech, storage/locks; AWS mocked.
- codex_helper/: ignored helpers/logs; validate-{a3,a5,b1,b4}.ps1 uses disposable services.

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
  PDF defaults100MB/500pages. Lesson UUIDs are job-specific; indexes zero-based;
  duration_seconds and viseme time_ms; provenance derives from source IDs.
- A1 retains originals/candidates/crops; strict JSON/end_turn comparison, priority
  review > unverified > checked. DONE means completion. Targets600tokens/60 overlap,
  preserves atomic structures/all page_numbers (page is first only).
- Checkpoints bind request/source/pipeline: corrupt503, pending409, expired410.
- lesson-v3-a3-8 needs no re-ingestion; new generation uses fresh idempotency key.
  UPRAISE_LESSON_CAPTURE_MODEL_OUTPUTS is local-only, removed by A6/TTL; logs omit text.
- Deadlines never skip source/reset on progress; max12h/default3 attempts.
  Config owns budgets; Linux parent-death/group shutdown supervises writers.
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
  storage remain open. No archive-read API. FUTURE_UPDATES.md: concurrency, DOCX/CSV, A7.
- Learning excludes expired/acknowledged outputs; grounding deferred (backlog4).
  False gate: codex_helper/b4-rag-synthetic-diagnosis.py/.log. B4 synchronous,
  WAV max60s/6MB. AWS tests invented-only; earlier real-course egress auto-review rejected.
- v2 uses PG/disk voice/SQLite Python calls: codex_helper/frontend-learning-comparison.md.

## Recent decisions

- 2026-10-07: A8/B1/B4 approved: v2 support, 24h playback, RAM learner state;
  factual grounding deferred, safety/language review/security preserved.
- 2026-10-08: User approved B5; keep course voice, clear session-only B4 reply voice.
- 2026-10-08: C1/C2 approved with cheap cached checks, scoped features, UNKNOWN and
  operator controls; C3 runtime administration remains deferred.

## User notes (preserve verbatim)

## Last verified

2026-10-08: C1/C2 final28 focused/STT checks and57 frontend checks passed;
broader310 backend checks passed earlier, with disposable PG/Qdrant/fake providers.
Ruff/Compose/diff passed. Additive migration and API/UI/all-worker refresh complete;
all7 healthy. Live /health,/ready,scoped /v1/status returned200: core/collection/
worker AVAILABLE, AWS UNKNOWN, readiness DEGRADED. Job3bb82b5e's12 chunks/WAV
hashes unchanged. Evidence: codex_helper/c-{backend-validation,stt-final,frontend-final,
deploy,deployed-check}.*. Prior B5 smoke204/retry, one content-free audit, course kept.
User B4 test returned correct power-grid18.7/12.4/page4, verified in Qdrant/PDF;
Italian43.44s WAV/565visemes, grounded=null. See b4-rag-test.json and
codex_helper/rag-source-page4.png. Expired Italian job47194bc7 retains14 PG chunks.
B1 requires unexpired A5; a RUNNING artifact lock may say JOB_CLEANUP_PENDING.
PowerShell5.1 needs Get-Content -Encoding UTF8; restart clears B1/end records.
