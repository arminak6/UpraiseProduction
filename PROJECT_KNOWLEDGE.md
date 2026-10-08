# Project Knowledge

## Purpose and current state

UPRAISE WP7 AI implements A1-A6, A8 listing, B1 start, B4 interruptions and B5 end.
V3 supports levels/translation (lesson-v3-a3-8); human approval is external.
PG retains text/SSML; A5 JSON/audio is temporary. Streamlit offers course playback.

## Architecture and data flow

- FastAPI exposes documents, lesson-jobs, job/result/audio, sessions and /health.
  MW owns pace/human approval; PG retains DRAFT text/SSML, Qdrant only sources.
  B2/B3/B6 and other original A/C paths remain reserved.
- B1 POST /v1/sessions: sessions:write + exact lesson_job_id. Separate production
  JWT binds client/subject/context/hash/approval; local DRAFT preview is allowed.
  Consent stays in RAM; B1 omits capabilities.
  session_started audit precedes visibility. New201; unchanged retry200 preserves
  state and never extends expiry.
- B4 POST /v1/sessions/{id}/interrupts: request-local LangGraph classify/retrieve/
  respond; no checkpoints/tracing. Native v2 teacher + exact PG context and current
  Qdrant evidence; questions search paired document/attempt filters, pinned versions.
  Factual grounding gate is temporarily disabled: grounded=null, context references,
  GROUNDING_NOT_VERIFIED. Separate strict-schema safety/session-language review
  applies to generated teaching/clarification before speech; rejection codes retain
  safe localized replies. Source-language queries and all source/auth checks remain.
  Text or RAM-only WAV -> EU Transcribe; questions consent/JWT required.
  Inline WAV/SSML/visemes or player instruction; TTS failure retains checked text.
  Content-free audit; one turn/session, default4 concurrent, bounded history,
  32MiB response cache, max100 messages/session; expiry never renewed.
- B5 POST /v1/sessions/{id}/end: shared B1/B4 auth without questions consent;
  reason COMPLETED/STOPPED_BY_LEARNER/TIMEOUT/ERROR, empty204. Clears session RAM,
  B4 reply audio/cache and counters; fences late B4 delivery. Content-free
  session_ended audit is idempotent; fixed TTL uses the same TIMEOUT cleanup.
  A3 WAVs, PG teaching text and Qdrant sources remain under their own retention.
- A8 GET /v1/lesson-jobs requires jobs:read. Client/course/active-source/DONE/
  unexpired/no-cleanup filters precede pagination; returns DRAFT metadata only.
- Frontend Learning uses A8/A5 with expiry/identity checks. B1/B4 pages support
  explicit start, consent/JWT, text/WAV and stable-ID retries. Learning Interrupt
  removes lesson audio; first message starts B1 then B4 on the current chunk.
  Continue rechecks A5 and applies replay/speed instructions.
  No position input; Continue replays the current part. Finish/Back send B5 and
  clear players/replies immediately; failed end keeps metadata for explicit retry.
  Manual End session (B5) page supports signed JWTs. B1 remains lazy in Learning.
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
  review before splitting/PG/target speech (A4 TRANSLATE). Notation/citations/code/
  pages/order remain immutable. Checkpoints avoid repeats; parser never repairs
  strings. Masked reviewer needs passage evidence; one correction/recheck.
- Beginner/intermediate/advanced (default intermediate) controls all3 role prompts;
  reviewer flags mismatches. A5 learner_level_applied=true, explanation-level-v1.
- V3 PCM/WAV matches SSML/visemes; overshoot rescaled/flagged, malformed marks fail.
  Actual engines/fallbacks are recorded.
- Workers use locks/leases/fencing/deadlines; provider calls are at least once.
  upraise_jobs/upraise_audit retain jobs/content-free audit; artifacts share volume.
- prepared_lesson_chunks retains final text before TTS and SSML before synthesis,
  with order/provenance/review/model versions. Fenced retries cannot change text.
- MW callbacks use pinned HTTPS/mTLS/OAuth; MW stores A5 then A6 RECEIVED.
  Callback2xx is no receipt. A5 checks scope/client/course/source/hash, shared locks
  and required audit; writers/cleanup stay exclusive.
- TTL24h from completion; A5 denies expired output immediately. Independent
  delivery recovers cleanup/callbacks; RECEIVED/CANCEL preserves sources/vectors.
- Disposal/A2 retains prepared_lesson_chunks, including failed/cancelled speech;
  no archive API/audio DB/backfill.
- A2 fences ingestion, purges all vector attempts and audits before204. A1 A6
  preserves source/active vectors/history; lesson A6 removes JSON/audio/checkpoints.

## Important paths

- FUTURE_UPDATES.md: ignored backlog: workers, DOCX/CSV, A7, B4 grounding, runtime scaling.
- README.md/compose.yaml/.env.example: setup; backend/docs/: contracts and decisions.
- backend/src/upraise_ai/: api/, schemas/, guards/, main.py expose/wire APIs;
  core/config.py owns signatures, budgets, providers and OAuth/TLS.
- api/runtime/, schemas/, workflows/{sessions,interrupts}.py, storage/sessions.py,
  guards/session_jwt.py, services/interrupt_{models,stt}.py: B1/B4/B5 runtime.
- services/pdf/: A1; lesson_*.py: A3/A5; v3/: original code/provenance.
- storage/, workflows/, workers/: persistence, acceptance/control, supervised work.
- frontend/{app,api_client,learning,learning_interrupt,session_start,learner_interrupt,
  session_end}.py: UI; tests/: fake HTTP. backend/tests/test_{a8,b1,b4,b5}*: runtime/catalog.
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

- B1 RAM dictionary: max10000 sessions, TTL1800s capped by lesson/JWT, sweep30s.
  Restart clears state; one backend process, no Redis/DB or activity renewal.
  B5 retains bounded content-free end records for up to30min; retries204 and
  recent ended IDs cannot reopen. Pending end audits retry but are RAM-only.
  Frontend progress is session-only; sources/drafts/images remain untrusted.
- Volumes: upraise_{qdrant_data,postgres_data,ingestion_data,model_cache}.
  Source content_hash is opaque caller metadata; source_hash is actual SHA-256.
  PDF defaults100MB/500pages. Lesson UUIDs are job-specific; indexes zero-based;
  duration_seconds and viseme time_ms; provenance derives from source IDs.
- A1 retains originals/rejected candidates/crops; strict comparison JSON/end_turn
  gates acceptance. Quality priority: review > unverified > checked. DONE is completion.
- A1 targets600tokens/60 forced overlap, preserves atomic structures; page_numbers
  retains all source pages while page is only the first.
- Checkpoints bind request/source/pipeline: corrupt503, pending409, expired410.
- Lesson identity lesson-v3-a3-8; no source re-ingestion required. New generation
  uses a fresh idempotency key. UPRAISE_LESSON_CAPTURE_MODEL_OUTPUTS captures replies
  only in local development; A6/TTL removes diagnostics. Default logs omit text.
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
- Translation/math/source defects: codex_helper/italian-v8-assessment-47194bc7.md
  and level-assessment/assessment.md. Beginner U+0008 invalidated SSML; original V3
  warns no-audio, A3 fails (beginner-retry-diagnosis.md/original-speech-comparison.md).
  AWS errors lose provider codes; MW callbacks unverified.
- Gateway/Moodle, HA/backups, retention/erasure, orphan cleanup and cross-host
  storage remain open. No archive-read API. FUTURE_UPDATES.md: concurrency, DOCX/CSV, A7.
- Learning excludes expired/acknowledged outputs. DRAFT/source/speech defects remain.
  B4 factual grounding is deferred (FUTURE_UPDATES.md section4); original gate's
  false rejection: codex_helper/b4-rag-synthetic-diagnosis.py/.log. Synchronous JSON,
  no streaming guarantee; WAV max60s/6MB. Agent AWS tests use invented material;
  earlier real-course agent egress was auto-review rejected.
- v2 uses PG/disk voice/SQLite Python calls: codex_helper/frontend-learning-comparison.md.

## Recent decisions

- 2026-10-07: A8/B1/B4 approved with v2 support, 24h playback and RAM learner state.
- 2026-10-07: User deferred B4 factual grounding; keep safety/language review,
  nullable grounded metadata and existing input/output/security contracts.
- 2026-10-08: User approved B5; keep course voice, clear session-only B4 reply voice.

## User notes (preserve verbatim)

## Last verified

2026-10-08: B1/B4/B5 validation129 passed with disposable PG/Qdrant and fake
providers; frontend55 passed. Ruff/Compose/diff checks passed. Rebuilt/redeployed
backend/frontend; all7 services healthy. Local B1/B5/A5/A8 smoke:201 then204 on
end/retry, identical course WAV/chunks, ready lesson retained. Read-only PG:
one content-free session_ended audit and12 retained chunks for job3bb82b5e.
Evidence: codex_helper/b5-{integration-final,frontend-final,build,deployed-check}.*.
User B4 test returned correct power-grid18.7/12.4/page4, verified in Qdrant/PDF;
Italian43.44s WAV/565visemes, grounded=null. See b4-rag-test.json and
codex_helper/rag-source-page4.png. Expired Italian job47194bc7 retains14 PG chunks.
B1 requires unexpired A5; a RUNNING artifact lock may say JOB_CLEANUP_PENDING.
PowerShell5.1 needs Get-Content -Encoding UTF8; restart clears B1/end records.
