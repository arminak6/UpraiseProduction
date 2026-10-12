# Project Knowledge

## Purpose and current state

UPRAISE WP7 implements A1-A8, A3 v2, B1/B2/B4/B5 and C1/C2/C5. Streamlit is an
HTTP testbench/player, defaulting to its A-series ingestion workspace.
PostgreSQL retains DRAFT text/SSML; Qdrant holds sources;
A5 JSON/WAV expires independently (24h). Middleware/Moodle owns human approval.
Live v2.5 job8d97f91e DONE/attempt1:7units/level,21 PG chunks/WAVs verified.
It remains DRAFT; semantic quality needs review. Earlier job6c8d3bc5 text review
found partial coverage and uneven level separation.
B3/B6/C3/C4 remain reserved. Original ../Uprise/v2 is reference-only.

## Architecture and decisions

- frontend/ingestion_workspace.py: default4-stage HTTP orchestrator A1–A8. A1
  suggests editable course/activity IDs; no setup step. A2 list/remove, optional
  course filter or all authorized owned sources; progress0..1 shown to2decimals.
  Draft loads A2 on entry, one source/v2-only/read-only autofilled IDs; no order,
  manual UUID/v1/trace controls. Individual API tools retain v1/v2. Jobs starts
  with Course→Level→Teaching chunk→Download voice; auto unfiltered/paginated A8,
  generations deduplicated by job. Deferred WAV/SHA256 on click; no player/JSON/ZIP.
  Manual UUID/poll/A6 in Advanced job tracking; A7 remains. Retry keys survive navigation/
  renewed signed URLs; changed context resets inputs, credential binding clears
  workspace. Last100 sanitized calls in captured trace list. No auto mutations.
- A1 Docling/vision/math extraction once -> ordered Qdrant chunks; active attempt
  prevents mixing source revisions. Returns document_id and job_id; A2 list/delete.
- A3 v1 POST /v1/lesson-jobs: one requested level; ordered5page windows -> original
  V3 generator/reviewer/editor -> checked translation if A1/A3 languages differ ->
  ~200word chunks -> PG text before Polly, exact SSML before TTS. docs/a3.md.
- A3 v2 /v2/lesson-jobs: canonical material once -> shared plan -> original
  generator/reviewer/editor adapts all configured levels; approved draft retained,
  negative review edited. Translation preserves units. Post-edit independent
  material review deferred (FUTURE_UPDATES.md); DRAFT/human review always applies.
  Same count/order/content_unit_id/pages/scope, distinct chunk IDs/text/audio.
  All texts/plan saved before speech. Target200/max500; bounded shortening and
  max2 shared plan refinements, never level-only splits. A1/old courses unchanged.
  v2.5/promptv6 validates IDs inside draft/review/edit/shorten response recovery;
  one correction, max3calls if format fallback precedes IDs, max2 otherwise.
  Ambiguous/truncated data fail closed; content-free stage/count diagnostics.
  Original V3 math/speech recovery retained. docs/a3-v2.md.
- A4 v2 counts are per level plus total. A5 bundle or ?learner_level=beginner,
  one root integrity hash; audio IDs unique across levels. A8 expands levels before
  pagination. A6 receipt/cancel/TTL fences writers, removes all temporary artifacts;
  durable PG text/SSML/shared plans/audit survive. docs/a5.md/a6.md/a8.md.
- A7 teacher_review + job/chunk -> exact PG/pinned sources -> three roles/translation/
  TTS -> new complete standalone revision of selected level. Parent unchanged;
  stable count/order/unit IDs, v2 material/max-size checked again. Structural
  changes need new A3 plan. Request idempotency explicitly deferred. docs/a7.md.
- Learning: separate lesson/difficulty selectors; Start lesson creates B1 before
  text/voice playback. Next chunk retains session; browser component pauses and
  captures exact currentTime on User interruption. Text questions use B4 with
  offset; Back to course rechecks A5/resumes saved position, honoring explicit
  REPLAY/PAUSE/SET_SPEED. WAV hash checked. B5 finish/end/error; no automatic B2
  measurement. Browser positions/session/replies RAM-only. lesson_audio.{py,js},
  learning_session.py; Docker includes assets. Standalone API tools preserved.
- B1 exact job/context/hash/approval JWT plus selected v2 learner_level; fixed30min
  RAM-only max10000, audit before visibility, no capabilities. Local DRAFT preview.
  Inherits language/voice/profile/source scope and only selected variant's chunks.
  Service OAuth/mTLS and separate session JWT are both required in production.
- B2 chunk-end: lesson_id/chunk_id/cognitive_load_score finite0..1; telemetry consent,
  >.5 HIGH_COGNITIVE_LOAD else PROCEED. No consent discards score. RAM-only score/
  index, content-free ID/version audit; test-v1 threshold, fusion/B6 absent.
- B4 request-local LangGraph classify/retrieve/respond; current PG chunk and
  Qdrant paired document/attempt scopes, source-language query rewrite. Questions
  consent/JWT required; text/WAV-STT -> text/SSML/WAVbase64/visemes or playback
  control. TTS failure keeps text. User deferred grounding: grounded=null and
  GROUNDING_NOT_VERIFIED; safety/language remain. One turn/session, max4concurrent,
  32MiB/100-message RAM cache, WAV60s/6MB; no learner disk/tracing.
- B5 reason COMPLETED/STOPPED_BY_LEARNER/TIMEOUT/ERROR ->204; clears RAM/B4 audio/
  chat/counters, fences late commits. Idempotent audit and30min retry metadata;
  course WAV/PG/Qdrant keep independent retention. Fixed TTL uses same cleanup.
- C1 /health dependency-free; /ready corePG/Qdrant/artifacts200/503 plus degradation.
  C2 /v1/status nullable feature reasons/kill switch; UNKNOWN intentional without
  recent provider evidence. Shared observations provider15min/transient30s/worker150s;
  no AWS polling. Gates block A3/A7, TTS-off keeps B4 text; B5 remains available.
- C5 GET /v1/version: jobs:read startup metadata; no provider/storage GET calls.
  Build/model/prompt/pipeline/profile hashes, threshold .5/fusion absent; common
  configuration_id API/workers. New-job provenance and full content-free audit
  snapshots. A7 keeps parent refs/adds revision refs. Checkpoints bind config refs.

## Key files and artifacts

- backend/src/upraise_ai/{api,schemas,workflows}/: API contracts/orchestration.
- services/lesson_*.py and v3/: original adapter/provenance/math/speech;
  services/aligned_{models,pipeline,responses}.py and schemas/aligned_lessons.py: A3 v2.
- storage/lessons.py: fenced jobs/permanent chunks/shared plans; uniqueness on
  (job_id,level_key,index), legacy level_key=''; repeatable additive migration.
- services/revision_{models,pipeline}.py, workflows/revisions.py: immutable A7.
- workflows/{sessions,interrupts}.py, storage/sessions.py, guards/session_jwt.py:
  runtime; services/interrupt_{models,stt}.py. core/versions.py: actual B2 policy.
- services/pdf/: A1; operations modules C1/C2; services/versions.py + schemas/versions.py C5.
- backend/docs/{a1,a2,a3,a3-v2,a5,a6,a7,a8,b1,b2,b4,b5,c1-c2,c5}.md: full contracts.
- frontend/{app,ingestion_workspace,learning,learning_interrupt,learning_session,lesson_audio}.py: HTTP UI;
  frontend/tests/: AppTest/fake HTTP. No direct provider/DB use.
- backend/{pyproject.toml,uv.lock,Dockerfile,math_runtime/}: pinned builds/math.
- codex_helper/: ignored scripts/logs/reports; FUTURE_UPDATES.md ignored backlog.
  Latest full-level review: level-content-assessment.md; older reports retained.

## Commands, environment and invariants

- Docker Desktop Linux containers; API8000/UI8501. docker compose config --quiet.
- Build: docker compose build backend frontend; additive migration:
  docker compose run --rm --no-deps migrate. Refresh API/UI/workers together:
  docker compose --profile tools up -d --no-deps --wait --wait-timeout 180 backend frontend worker lesson-worker lesson-delivery
- Drain jobs before pipeline/config changes; restart clears RAM sessions. Never reset
  live volumes/re-ingest sources for A3. No staging/commits without explicit request.
- Isolated validation helpers validate-a3v2.ps1/validate-c5.ps1 use only labeled
  disposable PG/Qdrant, fake providers, no user volumes/credentials. Dev image
  upraise-b4-validation; frontend tests in README. One-offs under codex_helper.
- Python3.12.14/uv.9.5/UID10001; Docling2.115/core2.99/EasyOCR/CPU Torch;
  NovaLite figure/NovaPro formula/fidelity, TitanV2 normalized1024D. A3 EU GPTOSS120B
  + Qwen235B, eu-central-1 default. LangGraph1.2.14/Transcribe.6.4; PG17.11/Qdrant1.19.1;
  Streamlit1.64/httpx.28. Node22/MathJax4.1.3/SRE5rc4.
- Polly neural16kHz mono16bitWAV; per-request visemes fitted/offset by PCM duration.
  SSML limits2800billed/5800total chars. EN/IT/FR/DE math smoke tested.
- UPRAISE_LESSON_LEVEL_PROFILES ordered JSON (1–8 named profiles), plan level must
  match; batch4/max8. Explicit level array preserves order across PG JSONB.
- Windows PS5.1 JSON reads need -Encoding UTF8. Curl JSON examples one line with
  curl.exe --%; use returned IDs, never NEW_JOB_ID placeholders.
- Chunk identity(job_id,chunk_id), index0-based; shared content_unit_id links levels.
  Source content_hash opaque/source_hash realSHA256; maxPDF100MB/500pages.
- Logs/audit omit texts/scores/audio/prompts/private provider exceptions. Optional
  model diagnostics local-only and deleted by A6/TTL. Source content untrusted.
- Fixed session TTL capped by lesson/JWT, no Redis/renewal. Locks/source ownership/
  leases fence publication; corrupt503/pending409/expired410. Attempts3/max12h,
  supervised killable children; no deadline reset on progress.
- Scopes documents:read/write, lessons:write, jobs:read/write, sessions:write;
  inaccessible404/conflict409/malformed422. Auth closes on missing production config.

## Limits and next work

- One ingestion/lesson worker; pools overlap but each processes jobs sequentially.
  A3 v2 variants sequential within bundle; concurrency backlog. No archive-read API;
  A5 expiry affects playback even with permanent PG text. Human assessment needed.
- DOCX/CSV, A7 idempotency, structural/cross-level edits and mid-session difficulty
  switching future. Gateway/Moodle callbacks/model access, HA/backups/erasure/orphan
  cleanup/cross-host storage unverified. Beginner control-char SSML issue diagnosed
  in beginner-retry-diagnosis.md; original warns while A3 fails.
- Real-course AWS egress test auto-review rejected; agent checks use invented
  data/fake providers. User initiates real curl/UI generation. Grounding backlog4.
- Failed A3 v2 retains checkpoints, no published PG/audio; uncaptured model
  responses cannot be reconstructed. Post-edit independent A3 review deferred;
  A7 retains alignment review. Earlier6c8d3bc5 had source gaps, weak difficulty
  separation, SSML/format/review leaks: level-content-assessment.md. WAV integrity
  does not certify semantics; human review required.

## User notes (preserve verbatim)

## Last verified

2026-10-12 Learning session update: learning/player/session/interrupt modules,
Docker assets, frontend tests and B1/B4/UI docs checked.87tests/Ruff/diff/build
passed; frontend deployed/healthy. Chromium fake-provider flow captured11432ms,
resumed11.827s with playback elapsed; unrelated rerun kept14.321s. One B1/B4/B5,
answer-only audio during interruption. Live existing8d97f91e advanced parts1/2
A8/A5/B1(201)/B5(204) passed; temporary session closed, no provider calls.
Evidence codex_helper/learning-session-verification.md, learning-browser-verification.json,
learning-live-verification.json, learning-session-tests.log and browser PNGs.

2026-10-12 A7 job61a2c008 DONE/attempt1: beginner part1 revision1 of8d97f91e.
Tables/numeric rows removed; changed WAV hash/PCM/duration valid; other6 texts/
SSML/audio hashes unchanged.9checks; DRAFT. a7-61a2c008-verification.{md,json}.

2026-10-12 v2.5/promptv6 fix:196isolated checks/build passed, C5 API/workers
match; alignment-recovery-fix.md. User8d97f91e DONE/attempt1 at11:38:31UTC,
IDs/MAX_TOKENS recovered:7units/level,21PG/WAVs,17checks, DRAFT/170findings.
A5 expires2026-10-13 11:38:31UTC; no semantic/audio listening review.
job-8d97f91e-completion-verification.{md,json}; old5da7620d v2.4 FAILED.

2026-10-12 A1 fa70751d/course383861 DONE/31pages/53Qdrant chunks;26checks passed.
NEEDS_REVIEW:19flagged/18unverified/16auto chunks;6provider validation warnings;
pages5/10 title-only/blank. latest-ingestion-verification.{md,json}.
Course voice picker:89tests/build/live part7 hash passed; course-voice-picker-verification.md.
A2:12integration checks. Named evidence above is under codex_helper/; dated
checks do not imply permanent output availability or semantic approval.
