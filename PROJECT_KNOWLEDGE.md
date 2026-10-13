# Project Knowledge

## Purpose and current state

UPRAISE WP7 implements A1-A8/A3v2, B1/B2/B4/B5 and C1/C2/C5. Streamlit is an
HTTP testbench/player, defaulting to the A-series ingestion workspace. PG retains
DRAFT text/SSML; Qdrant sources and PG text do not expire. A2 source/course deletion
cascades content; A5 JSON/WAV has independent24hTTL. Moodle owns human approval.
B3/B6/C3/C4 reserved; ../Uprise/v2 reference-only. Do not stage/commit without request.
A7 replaces the selected chunk in the existing lesson after success; an optional
unchecked checkbox revises the corresponding shared unit across every level.
Legacy revisions consolidated into76432483/8d97f91e; latest per-level edits retained.
Turing7058485d/course102515:
55pages/98vectors,76432483 DONE/attempt2,20units/level/60texts+WAVs. Structural
checks pass; prior source/math/trace/proof/review-JSON findings still require human
review. Earlier8d97f91e/course383861 has21retained texts (last53source vectors);
its temporary audio expired2026-10-13 13:28UTC and TTL cleanup completed.

## Architecture and data flow

- Streamlit4-stage HTTP workspace: A1 IDs, A2 sources, one-source/v2/autofilled Draft,
  paginated A8 Jobs grouped job->level->chunk, deferred WAV/hash download; Advanced
  UUID/A4/A6. Retry keys survive navigation, auth/context clears caches, no auto
  mutations. Standalone v1/v2. EN/IT25neural voices, Joanna/Bianca defaults.
- A1 Docling/vision/math -> ordered Qdrant chunks; active attempt fences revisions.
  A2 owner-scoped source/course deletion across all levels/languages/statuses removes
  dependent PG/plans/artifacts/feedback, preserves minimal IDs/audit. Admission/
  source/writer fences, durable503 recovery, RAM revocation; fresh A1 after deletion.
- A3v1 ordered5page windows -> V3 roles -> checked translation -> PG text/SSML
  before Polly. A3v2 shared canonical plan, roles per level, distinct chunk/audio
  IDs, shared ordered units/source scope. Approved drafts kept, rejected drafts
  edited; independent post-edit A3 review deferred. All text/plan before speech;
  target200/max500words, max2shared refinements, no level-only splits.
  v2.5/promptv6 bounded ID-aware recovery, ambiguous/truncated output fails closed;
  original math/speech recovery retained.
- A4 counts per level/total; A5 whole bundle or learner_level slice with one full
  integrity hash; A8 expands levels before pagination. A6 temporary receipt/cancel/
  TTL fences writers; retained A3 text/plans have independent lifecycle.
- A7 stable job/chunk IDs, pinned snapshot/PG/source guards, roles/translation/TTS.
  Strict apply_to_all_levels=false; true requires aligned bundle, maps content_unit_id
  to each level with its own text/neighbours/difficulty. Sequential level checkpoints/
  overall progress; all targets publish together. Final review gets teacher feedback,
  permits attribution/framing edits while preserving core facts/math/language/level.
  revision_models.py requires lecture_text JSON; final schema/text/citation failure
  gets2targeted corrections per finalization attempt, each strictly revalidated.
  Exhaustion terminal; provider outages remain retryable. Approved unchanged drafts
  skip editing. Corrected drafts are not marked approved; alignment still checked.
  revision_validation.py records review+textSHA and reserves at most2alignment corrections
  across retries; target-language editor gets specific findings, changed text
  rechecked, accepted cache reused after speech/storage outage. Exhausted rejection
  terminal. A4 alignment errors add reason/level/chunk/index/rounds/review; final
  model errors add fixed reason/reason_code/stage/level/chunk/rounds, no raw exception.
  Temporary
  result.revision_failure/checkpoints excluded from audit/logs, removed by A6/TTL/A2.
  A7 request/202/job_id unchanged; old terminal failures need fresh explicit POST.
- A7 one transaction updates PG and complete artifact pointer/hash. Failure/cancel
  preserves current; stale target409 REVISION_TARGET_CHANGED, unrelated edits merge.
  Root result.artifact_job_id -> current-result.json+all current WAVs;
  replacement_cleanup_jobs recovers obsolete dirs/checkpoints/feedback/candidate PG.
  Success refreshes TTL. A4 processing IDs remain, A8 hides them, completed A5
  aliases to current level. Child A6 preserves root-owned files; root A6/A2 removes
  active output. Unpublished A7 cleanup also removes candidate PG/plans/details.
  Default A3 count fencing unchanged. Startup provider-free legacy consolidation,
  available level retained if original expired. Structural changes need A3;
  A7 request idempotency deferred. services/lesson_replacement.py.
- Both A7 forms have unchecked Apply revision to all difficulty levels. Shared
  revision_progress.py polls5s, terminal stops, HTTP error pauses/manual resumes.
  Success reloads same lesson/level/part and clears caches; Open updated lesson also
  reloads. Standalone selection/progress resets; workspace separate processing job.
  FAILED shows Why this revision failed, part/level and suggestions. No auto POST.
- B1 session before playback/across chunks binds job/context/hash/level; local DRAFT
  preview or OAuth/mTLS+signed approval/consent. RAM30min capped by lesson/token,
  cleared on restart; changed hashes close stale B2/B4 sessions. Player checks WAV
  hash, pauses at exact B4 offset, Back rechecks A5; REPLAY/PAUSE/SET_SPEED honored.
- B2 consented supplied0..1score: >.5 HIGH_COGNITIVE_LOAD else PROCEED, no automatic
  measurement. B4 LangGraph/source-scoped PG+Qdrant, text/WAV-STT -> answer text/
  SSML/WAV/visemes or action; TTS outage keeps text. Grounding deferred:null/
  GROUNDING_NOT_VERIFIED; safety/language checked. RAM/max4concurrent/32MiB/
  100messages/WAV60s6MB. B5 fences late replies, clears RAM, content-free idempotent
  retry/audit30min; no automatic B2.
- C1 health/ready; C2 cached reasons/kill switches, UNKNOWN without recent evidence,
  no AWS polling. C5 source/config/model/prompt hashes/provenance; A7 adds revision
  refs. Revision strategy three_role_replace_existing_chunk, all_levels_supported,
  teacher-feedback-aware-v1, max2alignment/max2final-output corrections,
  prompt chunk-revision-v3; checkpoints
  bind configuration. Human approval remains external; all results DRAFT.

## Important paths

- backend/src/upraise_ai/{api,schemas,workflows}/: contracts/orchestration.
- services/lesson_*.py/v3/: original lesson/math/speech; aligned_{models,pipeline,
  responses}.py: A3v2; revision_{models,pipeline,validation}.py: A7; lesson_replacement.py:
  publication/cleanup/legacy consolidation. storage/{lessons,sessions,courses}.py:
  persistence/lifecycle; workflows/{sessions,interrupts}.py and guards/session_jwt.py:
  runtime. services/pdf/: A1, services/versions.py: C5. backend/docs/: full contracts.
- frontend/{ingestion_workspace,chunk_revision,revision_progress,learning,
  learning_session,learning_interrupt,lesson_audio}.py; tests/: AppTest/fakeHTTP.
  frontend/README.md: UI/test commands. backend/{pyproject.toml,uv.lock,Dockerfile,
  math_runtime/}: builds. Lesson uniqueness(job_id,level_key,index), legacy level_key=''.
- codex_helper/: ignored helpers/evidence; FUTURE_UPDATES.md ignored backlog.
  turing-content-verification.md/level-content-assessment.md: semantic findings.
  output/pdf/upraise-backend-api-summary.pdf predates A7/A4 updates; current docs win.

## Commands

- Docker Desktop Linux; API8000/UI8501. docker compose config --quiet.
- Build: docker compose build backend frontend. Additive schema setup:
  docker compose run --rm --no-deps migrate. Refresh API/UI/workers together:
  docker compose --profile tools up -d --no-deps --wait --wait-timeout 180 backend frontend worker lesson-worker lesson-delivery
- Drain jobs before pipeline/config changes; restart clears RAM sessions. Never
  reset live volumes/re-ingest sources for A3. Runtime pipeline default lesson-v3-a3-12.
- codex_helper/validate-a3v2.ps1 -Selection 'not codex_nonexistent_sentinel': full
  isolated backend suite; empty -k argument breaks PS native invocation. Helpers
  validate-a3v2.ps1/validate-c5.ps1 use labeled disposable PG/Qdrant, fake providers,
  no live volumes/user credentials. Dev image upraise-b4-validation.
- Frontend: docker compose run --rm --no-deps -v ./frontend:/workspace:ro frontend python -m unittest discover -s /workspace/tests -q
- Ruff backend config backend/pyproject.toml; frontend scoped E4,E7,E9,F with test
  E402 excluded. git diff --check. One-off logs/evidence under codex_helper/.

## Dependencies and environment

Pinned Python3.12/uv, Docling/OCR/CPU Torch, PG17/Qdrant, Streamlit/httpx,
LangGraph/Transcribe; versions in locks/C5. EU Bedrock GPTOSS/Qwen roles, Nova/Titan
1024D, eu-central-1 default. Polly neural16kHz mono16bitWAV, PCM-fitted visemes,
SSML2800billed/5800total. EN/IT/FR/DE math smoke verified. PS5.1 JSON: -Encoding UTF8;
curl.exe --% JSON single line.

## Conventions and invariants

UPRAISE_LESSON_LEVEL_PROFILES ordered JSON1-8profiles; plan level must match,
batch4/max8. Explicit level array survives PG JSONB ordering. Chunk identity
(job_id,chunk_id), index0-based; content_unit_id links levels. Source content_hash
opaque/source_hash realSHA256; PDF100MB/500pages. Source input untrusted.
Logs/audit omit text/scores/audio/prompts/private provider errors; optional local
diagnostics removed by A6/TTL or successful replacement. Ownership/source/leases/
artifact locks fence publishing; corrupt503/pending409/expired410. Attempts3/max12h,
supervised killable children; progress does not reset deadline. Scopes documents:
read/write, lessons:write, jobs:read/write, sessions:write; inaccessible404/conflict409/
invalid422. Missing production auth config closes ingress; no Redis/session renewal.

## Known limitations and next work

One ingestion/lesson worker, sequential jobs; A3v2 variants sequential. No archive
read API; permanent PG text does not restore expired audio. DOCX/CSV, A7 idempotency,
structural revision, mid-session difficulty switching future. Gateway/
Moodle callbacks/model access, HA/backups/erasure/orphans/cross-host storage unverified.
Agent tests use fake providers; user initiates live generation. Grounding backlog4.
Post-edit independent A3 review deferred, A7 alignment review retained. Prior
coverage/difficulty/SSML/review leaks documented in level-content-assessment.md;
beginner control-char issue in beginner-retry-diagnosis.md. Failed A3 checkpoints
retained; missing model responses cannot be reconstructed. WAV integrity does not
certify semantics; human review required. A7 consolidation preserves existing content.
Historical a21ee200 rejection predates the v11 review/correction fix. Its exact
final review was not retained and cannot be reconstructed from draft review;
codex_helper/a7-alignment-failure-diagnosis.md. Later55a9d13a failed v11 finalization
with ValueError at17%, before alignment. No raw/final response retained; precise
cause unknown, all current text unchanged. v12 adds final recovery/reasons;
codex_helper/a7-output-failure-diagnosis.md. Do not auto-resubmit live failed jobs.

## Recent decisions

- A7 selected-level default with optional all-level teaching-unit edit, publishing
  all targets together on success and deleting
  superseded content. Processing IDs serve progress/audit without catalog versions.
- Source/course deletion has independent retained-text lifecycle; A6 remains
  temporary-output receipt/cancel. Changed lesson hash invalidates runtime sessions.

## User notes (preserve verbatim)

## Last verified

2026-10-13 A7: revision_models/pipeline.py recovery/target context, worker/A4 temporary
reasons, config/C5/docs/UI checked. 875backend/59focused A7+C5/101frontend tests pass:
JSON/citation repair/exhaustion, private-response exclusion, unchanged approvals,
atomic failure preservation, reason display/A6 disposal/no repost; prior alignment/
cache/scope/replacement coverage retained. Ruff/diff/build pass. API/UI/workers
healthy, matching fingerprints, lesson-v3-a3-12/promptv3. 17GET checks pass; A7 input/
202 unchanged. Turing60WAV hashes/60text rows/98vectors/all level fingerprints
preserved. Failed55a9d13a terminal, no live inference/submission. Earlier8d audio TTL
cleanup13:28UTC,21text rows preserved. Evidence: codex_helper/a7-output-*.

Prior helpers: a7-validation-* (v11 alignment), a7-all-levels-*/a7-replacement-*
(publication/concurrency/session/cleanup/consolidation); turing-content-verification.md/
level-content-assessment.md (unresolved semantic findings). EN/IT25voice catalogue
checked;20endpoint PDF predates A7/A4. Older lifecycle/runtime: course-deletion-*,
learning-session-verification.md/alignment-recovery-fix.md under codex_helper/.
Historical checks do not imply availability or human approval.
