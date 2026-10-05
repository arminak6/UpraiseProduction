# Project Knowledge

## Purpose and current state

UPRAISE WP7 AI provides A1 ingestion, A2 material management, A3 draft lessons,
A4 status, A5 result/audio and A6 cancellation/receipt. Local A3 is reachable;
native V3 teacher/speech implementation is restored as lesson-v3-a3-4.
A live V3 job completed first attempt:30 audio chunks verified. Content still
requires human approval. A5 pull downloads support audit, TTL and parallel reads.

## Architecture and data flow

- FastAPI: /v1/documents (POST/GET/DELETE by ID), POST /v1/lesson-jobs,
  GET/DELETE /v1/jobs/{job_id}, GET .../result and .../audio/{chunk_id}, /health.
  MW owns permanent lessons, learner state, pace and human approval. Qdrant stores
  source material only. Other original A/B/C paths remain reserved.
- INGRESS enforces OAuth JWT/client/course/scopes and trusted mTLS assertions;
  proxy headers are disabled. Explicit development + local_test_mode grants
  local-test-client authoring access only to actual private/loopback peers.
- A1 stages bytes with fsync, commits an operational job and returns202 with only
  document_id; Location is A4. User removed Ingestion Guard/hash/origin scans.
  PDF limits/support and HTTPS Moodle destination controls remain.
- Ingestion: Docling/OCR -> Nova candidates/source comparison -> quality-aware
  chunks -> Titan -> isolated Qdrant attempt/count -> audit -> DONE.
  Undeleted DONE + active_attempt is activation authority; vector tokens must match.
- A3 returns202 job_id, waits known pending A1 jobs, rejects unknown IDs (use A1),
  scrolls all active chunks and preserves requested document/page/chunk order.
  Cross-window chunks are assigned once with all source pages. Defaults:5page
  source windows, approximately200word output chunks; no silent source truncation.
- A3 copies services/v3/: original explain -> review_explanation ->
  finalize_explanation. Bad draft citations reach review; final checks occur in
  finalization. ONE editor revision if rejected; no extra Nova PDF judge or
  post-edit model review. Whole-lesson reports aggregate the pre-edit reviewer
  findings and source/speech flags. Business status stays DRAFT.
- Language controls text/speech; voice compatibility uses an explicit EU region.
  Learner levels are recorded but do not change generation. V3 batches MathJax/
  ClearSpeak conversion and contextual generator/judge narration. Its deterministic
  or structural fallback is retained if narration fails, with a speech finding.
- Original outputConfig/prompt-only fallback, prose normalization and final
  citation/math checks are preserved. A3 paragraphs retain whole-window source
  provenance; spoken cleanup removes Markdown/citations while protecting math.
- A3 wraps native V3 PCM/WAV synthesis with matching SSML/viseme requests. Valid
  timing overshoot is rescaled to PCM length and flagged; malformed/empty marks
  fail. Actual engines, including V3's standard fallback, are recorded.
- Independent worker, lesson-worker and lesson-delivery services use killable
  spawned processes, artifact locks, leases, fencing and supervised deadlines.
  Provider calls are at least once. upraise_jobs holds operational ingestion_jobs/
  lesson_jobs; upraise_audit holds separate content-free audits. Sources, crops,
  checkpoints and temporary lesson JSON/audio are on the shared volume.
- MW callbacks use allowlisted pinned HTTPS, mTLS and short-lived OAuth tokens.
  Basic/POST/certificate client auth supported. Callback2xx is not receipt;
  MW stores A5 output then sends A6 RECEIVED. Secrets remain deployment-only.
- A5 uses jobs:read ingress, client/course isolation and source/hash checks.
  Shared artifact locks allow parallel reads; generation/cleanup stay exclusive.
  Every prepared JSON/WAV request records a content-free access audit; audit
  outages block delivery. Responses use no-store. Pull does not acknowledge A6.
- Output TTL defaults24h from completion. A5 denies expiry immediately; independent
  delivery recovers deletion/callback retries. RECEIVED/CANCEL purge lesson output
  only, preserving sources/vectors. Durable markers/audit IDs recover cleanup.
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
- backend/{pyproject.toml,uv.lock,Dockerfile}, math_runtime/{math_speech.mjs,
  package.json,package-lock.json}: pinned builds and local math runtime.
- callback_tls/README.md: ignored read-only secret mounts; real callback
  credentials not supplied or logged/built.
- Legacy V3: ../Uprise/v2/backend/learning/v1/.
- backend/tests/test_a3*.py: V3/lifecycle/speech/callbacks; test_a5_integration.py:
  pull/audit/concurrency/TTL, test_artifact_locks.py: Windows/Linux process locks.
  test_v3_original_*.py keeps original regressions; AWS mocked.
- codex_helper/: ignored comparison reports/checkpoints and validation scripts/logs.
  validate-a3/a5.ps1 uses disposable services; a5-*.log/runtime-check.json records A5.

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

- No learner state/permanent lesson business storage. Source/drafts/feedback/images
  are untrusted prompt data. Local auth never bypasses outbound callback security.
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
- Lesson identity is lesson-v3-a3-4; existing sources need no re-ingestion, and new
  generation uses a fresh idempotency key. Raw failed replies are opt-in via
  UPRAISE_LESSON_CAPTURE_MODEL_OUTPUTS, only development + local_test_mode; ordinary
  A6/TTL cleanup removes job diagnostics. Default logs contain no generated text.
- Budgets never change content identity or skip source. Ingestion max12h:
  parse180+30/pageOCR (10 without), enrich180+180/request, embed120+90/chunk.
  A3 max12h: overhead300 +1200/section or180/outputchunk; calibrate large PDFs.
- Progress never resets deadlines. Linux parent-death/group shutdown kills writers;
  bounded DB ownership readers supervise children. Retry maximum defaults3.
- A1 artifact retention is disabled by default; optional days preserve job/audit
  history. A3 TTL is independent. Artifact locks outside outputs are never unlinked.
  No completed cleanup/audit means no A6 204; busy writers503 with recovery.
- Scopes: documents:read/write, lessons:write, jobs:read/write. Inaccessible IDs404;
  key/action conflicts409, malformed422. A1 response contains only document_id.

## Known limitations and next work

- Live v1.4 false passes: diagram edges pages29/30, operator page10, marks pages9/44;
  strict guard rejects correct page46 repair. Fourteen verifier ValidationExceptions
  remain unexplained. See ingestion-1-vs-2-vs-3-report.md; broader PDFs pending.
- A3 reports do not guarantee global coherence. Learner level is reserved;
  huge windows fail explicitly, requiring smaller windows/request-budget tuning.
- Earlier adaptation added premature draft citation checks, a Nova PDF judge and
  a post-edit review. Native V3 replacement removes them. User job c6e896a6 failed
  at speech (18 checkpoints completed); exact malformed-mark reason is unknown
  because the rejected provider response was not retained.
- Live V3 job241c4bda completed first attempt; all30 audio hashes/durations/
  visemes verified. Its259 findings comprise94UNVERIFIED source flags and165
  pre-edit review findings; final content is not re-reviewed. A sampled final
  explanation adds claims unsupported by cited pages4-5. A5 parallel lock503
  was fixed with shared readers; writer conflicts still return retryable503.
  MW callbacks/other workloads remain unverified.
- Production gateway/Moodle, HA/backups, retention/audit erasure, orphan cleanup
  and cross-host artifact storage remain open; other APIs/learner RAG are reserved.

## Recent decisions

- 2026-10-05: User requested copying the working V3 implementation and adapting
  it to unchanged A3 inputs/results after repeated compatibility failures.

## User notes (preserve verbatim)

## Last verified

2026-10-05: A5 routes/workflow/audit/shared locks/docs checked. Backend371 tests
passed with disposable DBs/fake providers; A5 subset9 and native Windows locks3
passed. Ruff/Compose/whitespace pass. Four services rebuilt/restarted; all seven
healthy. Live existing job241c4bda JSON unchanged and all30 WAV hashes/durations
valid with8concurrent readers (32requests/200);33unique access audits recorded.
OpenAPI7paths/14schemas unchanged. Registry5FAILED/1DONE lessons; no new generation
submitted. V3 lesson-v3-a3-4/capture off; sources retained. TTL/A6/source integrity
covered in tests; human content approval and MW callbacks remain outstanding.
Evidence: codex_helper/a5-{validation,focused-validation,lint,build}.log and
a5-runtime-check.json; earlier lesson QA: a3-completed-lesson-verification.json.
