# Project Knowledge

## Purpose and current state

UPRAISE WP7 implements A1-A8, A3 v2, B1/B2/B4/B5 and C1/C2/C5. Streamlit is an
HTTP testbench/player, defaulting to its A-series ingestion workspace.
PostgreSQL retains DRAFT text/SSML; Qdrant holds sources;
Qdrant/PG text have no expiry; explicit A2 source/course deletion cascades content.
A5 JSON/WAV expires independently (24h). Middleware/Moodle owns human approval.
Live v2.5 job8d97f91e DONE/attempt1:7units/level,21 PG chunks/WAVs verified.
It remains DRAFT; semantic quality needs review. Earlier job6c8d3bc5 text review
found partial coverage and uneven level separation.
Turing source7058485d/course102515:55pages/98vectors, generation76432483
DONE/attempt2:20units/level/60texts+WAVs. Structural checks pass; confirmed
subtraction/table/ID-trace/proof errors and advanced reviewer JSON block approval.
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
- frontend/voice_picker.py: shared A3 EN/IT neural voice dropdown, also in
  standalone v1/v2 tools. Language/voice outside submit form refresh choices
  immediately; exact locale tags filter accents. Defaults Joanna/Bianca;
  other languages keep manual entry. Static25-voice catalogue verified via
  Polly DescribeVoices(eu-central-1,neural); reverify on engine/region changes.
- A2 document deletion fences/removes all dependent generation text/SSML/plans,
  temporary lessons and embedded A7 feedback/material (all languages/levels/statuses,
  including expired/A6-received). Course DELETE /v1/courses/{course_id} applies
  owner+course scope to all sources/generations. Minimal IDs/audit and original
  ingestion evidence retain independent policy. Course deletion barrier blocks
  A1/A3/A7 until completion; source locks at acceptance prevent orphan races.
  Busy writer/outage503 durable/retryable; worker recovers, UI drops cached content. Sessions
  revoked; sweeper catches worker recovery. Fresh ingestion after deletion allowed.
  storage/courses.py + lesson content_deletion_* fields/indexes, additive migration.
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
- frontend/revision_progress.py: shared A7 inline status/stage/percentage panel,
  automatic A4 GET every5s while page open; terminal/error stops, manual refresh
  resumes. Open revised lesson loads/selects new version; standalone retains
  edited part. Workspace panel remains visible after tracked job changes.
  A5-load and A7-submit HTTP details labeled separately; no automatic POST retry.
- Learning: separate lesson/difficulty selectors; Start lesson creates B1 before
  text/voice playback. Next chunk retains session; browser component pauses and
  captures exact currentTime on User interruption. Text questions use B4 with
  offset; Back to course rechecks A5/resumes saved position, honoring explicit
  REPLAY/PAUSE/SET_SPEED. WAV hash checked. B5 finish/end/error; no automatic B2
  measurement. Browser positions/session/replies RAM-only. lesson_audio.{py,js},
  learning_session.py; Docker includes assets. Standalone API tools preserved.
- B1 binds exact job/context/hash/level; local DRAFT preview or production service
  OAuth/mTLS + separate signed session JWT with approval/consent. Fixed30min TTL
  capped by lesson/token; bounded RAM only, cleared on restart. No capabilities.
- B2 chunk-end consumes supplied finite0..1 score; telemetry consent required
  to retain it, >.5 HIGH_COGNITIVE_LOAD else PROCEED. No model/automatic measurement.
- B4 LangGraph classify/retrieve/respond: PG chunk/Qdrant document+attempt scope,
  source-language rewrite, questions consent/JWT, text/WAV-STT -> text/SSML/WAV
  base64/visemes or player action. TTS outage keeps text. Grounding user-deferred:
  grounded=null/GROUNDING_NOT_VERIFIED; safety/language stay. RAM only, one turn/
  session, max4 concurrent,32MiB/100-message cache, WAV60s/6MB.
- B5 ends/clears RAM chat/reply audio/counters and fences late replies. Idempotent
  content-free audit/retry metadata30min; course artifacts keep independent policy.
- C1 health/ready; C2 cached feature reasons/kill switches, UNKNOWN without recent
  evidence. No AWS polling; A3/A7 gated, TTS-off keeps B4 text/B5. docs/c1-c2.md.
- C5 jobs:read startup version/config/build hashes, immutable generation provenance
  and content-free audit. API/worker config matches; A7 adds revision references;
  checkpoints bind configuration. docs/c5.md.

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
- Turing course review: codex_helper/turing-content-verification.md and
  turing-{ingestion,generation}-verification.json; retained review export/PDF.
- API input/output reference: codex_helper/output/pdf/upraise-backend-api-summary.pdf
  (5 pages, 20 application endpoints plus built-in docs); generator:
  codex_helper/create_api_summary_pdf.py. Recheck live OpenAPI before regenerating.

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
- Agent checks use invented data/fake providers; real-course AWS egress previously
  auto-review rejected. User initiates live generation. Grounding backlog4.
- Failed A3 v2 retains checkpoints, no published PG/audio; uncaptured model
  responses cannot be reconstructed. Post-edit independent A3 review deferred;
  A7 retains alignment review. Earlier6c8d3bc5 had source gaps, weak difficulty
  separation, SSML/format/review leaks: level-content-assessment.md. WAV integrity
  does not certify semantics; human review required.

## User notes (preserve verbatim)

## Last verified

2026-10-13 A7 progress UI: shared monitor, standalone/workspace, Docker inclusion
and README checked.94frontend fake HTTP tests pass, then3targeted tests after
job-field update; running/HTTP-pause/resume/DONE/open-result/navigation checked.
Ruff new module + scoped E4/E7/E9/F and diff pass. Frontend-only deploy healthy,
file hashes match; backend/worker start times unchanged. Existing A7 revision
40d2a50f DONE, advanced part1 text/SSML/audio hash changed, other19unchanged.
Evidence codex_helper/a7-progress-{frontend-tests.log,final-tests.log,
services-before.txt}, a7-40d2a50f-verification.json. No live revision submitted.

2026-10-13 Turing course verification: source/prepared98Qdrant records, PG60text/
SSML+shared20unit plan, 60WAV hashes/PCM/duration/visemes;26source+17generation
checks pass. PDF14/16/21/46 visually compared with final lessons; source16table
merged, input0^m1 0^n changed to0^m B1^n, part5trace drops0, part17omitsstates.
Advanced parts2/3/8/9 leak Review JSON; exercises/level task alignment partial.
59source warnings/331aggregate reviewer flags are not confirmed-error counts.
Content insufficient for approval; report scope is focused, not full certification.
No content mutation/provider calls; reports under codex_helper/turing-*.

2026-10-13 A3 voice selector: Polly read-only catalogue23EN/2IT, shared picker,
workspace/standalone forms, Dockerfile/.dockerignore and frontend README checked.
92frontend AppTest/fake HTTP tests passed; selected voice/language payloads,
navigation/retry fencing verified. New module Ruff/all changed files E4/E7/E9/F
(existing test E402 excluded) and diff check passed. Frontend-only build/deploy
healthy; deployed file hashes match. Backend/worker start times unchanged;
active generation76432483 finished DONE during deployment. Evidence:
codex_helper/voice-picker-frontend-tests.log and voice-picker-services-before.txt.

2026-10-13 API summary: live /openapi.json compared with backend api/ and schemas/;
A1/A3/B1/B2/B4/A6 contracts checked. All20 application endpoints included in
5-page PDF; text/route coverage and all rendered pages verified. B2 without
telemetry consent returns PROCEED and discards the score. Runtime code unchanged.

2026-10-12 coordinated deletion: source/course workflows/storage/API, B1 fence/
RAM revocation, recovery worker, frontend controls and docs checked.213isolated
backend tests/90frontend tests, Ruff/diff/build passed. New course DELETE and
schema deployed; no live course deleted. Before/after protected383861 retained
28PG text rows/2plans/53Qdrant vectors. Runtime lessonTTL86400/artifact retention0.
Evidence codex_helper/course-deletion-{backend-tests.log,frontend-tests.log,
verification.md,before.json,after.json}; check-deletion-deploy.py/UI guard GET-only.

2026-10-12 Learning session update:87tests/build, Chromium fake B4 captured11432ms,
resumed11.827s including elapsed playback, unrelated rerun kept14.321s. Live8d
advanced A8/A5/B1/B5 passed/closed; no providers. learning-session-verification.md.
A7 61a2c008 beginner part1 revision1 of8d removed tables/numbers;9checks/WAV hash
passed, other6unchanged, DRAFT. a7-61a2c008-verification.{md,json}.

2026-10-12 v2.5/promptv6:196checks/build, C5 aligned. alignment-recovery-fix.md.
8d97f91e DONE/attempt1 at11:38:31UTC, IDs/MAX_TOKENS recovered:7units/level,
21PG/WAVs,17checks, DRAFT/170findings; A5 expires2026-10-13 11:38:31UTC.
No semantic/audio listening review. job-8d97f91e-completion-verification.{md,json}.
Old5da7620d v2.4 FAILED. A1 fa70751d/course38386131pages/53vectors/26checks;
19flagged/18unverified/16auto chunks,6provider warnings, p5/10 title-only/blank:
latest-ingestion-verification.{md,json}. Voice picker89tests/live part7 hash
passed: course-voice-picker-verification.md. A2 prior12checks. Named evidence
is under codex_helper/; dates do not imply permanent output availability or approval.
