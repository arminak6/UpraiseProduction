# Project Knowledge

## Purpose and current state

UPRAISE WP7 implements A1-A8/A3v2, B1/B2/B4/B5/B7 and C1/C2/C5. Streamlit is an
HTTP testbench/player, defaulting to the A-series ingestion workspace. PG retains
DRAFT text/SSML; Qdrant sources and PG text do not expire. A2 source/course deletion
cascades content; A5 JSON/WAV has independent24hTTL. Moodle owns human approval.
B3/B6/C3/C4 reserved; ../Uprise/v2 reference-only. Do not stage/commit without request.
A7 replaces the selected chunk in the existing lesson after success; an optional
unchecked checkbox revises the corresponding shared unit across every level.
Legacy revisions consolidated into76432483/8d97f91e. Turing7058485d/course102515:
55pages/98vectors,76432483 DONE/attempt2,20units/level/60text+WAVs. Prior source/
math/trace/proof/review-JSON findings require human review. Earlier8d97f91e/
course383861:21retained texts; audio TTL cleanup2026-10-13 13:28UTC.

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
- A7 pinned job/chunk/PG/source snapshot, roles/translation/TTS. Strict all-level
  checkbox=false; true maps shared content_unit_id across available levels, using
  each level's own text/neighbours/profile, sequential checkpoints, atomic publish.
  Review gets teacher feedback; cosmetic attribution edits allowed, facts/math/
  language/level preserved. revision_models.py: strict JSON/final validation,
  2corrections per finalization attempt; approved unchanged drafts skip editing,
  corrected drafts not marked approved. revision_validation.py: review+textSHA,
  max2alignment corrections across retries; new text reviewed, accepted cache reused
  after speech outage. Exhaustion terminal, provider outages retryable. A4 failure
  reasons/level/chunk/rounds (alignment review or fixed safe model reason_code/stage)
  are temporary, excluded from logs/audit, disposed by A6/TTL/A2. Fresh POST for old
  failures; A7 request/202 unchanged. backend/docs/a7.md.
- A7 atomic PG/artifact pointer/hash replacement, failure/cancel preserves current.
  Stale target409 REVISION_TARGET_CHANGED; unrelated edits merge. artifact_job_id
  points to current-result.json+WAVs; replacement_cleanup_jobs recovers obsolete
  files/feedback/candidate PG. Success refreshes TTL. A4 processing IDs remain,
  A8 hides them, A5 aliases to current level. Child A6 protects current artifacts;
  root A6/A2 removes output, unpublished candidates also lose PG/plans/details.
  Provider-free startup consolidates legacy revisions without reviving expired
  audio. Structural edits need A3; A7 idempotency deferred. lesson_replacement.py.
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
- B7 DELETE /v1/lesson-jobs/{lesson_job_id}, optional learner_level, lessons:write;
  omit to delete whole lesson. Sources/other lessons preserved; last level deletes
  whole. Durable lesson_content_deletions + deleted_levels fence pending reads/
  revisions; delivery maintenance recovers503, repeated DELETE204/no duplicate audit.
  Partial PG+manifest/hash switch preserves siblings/IDs/expiry/shared plan; old
  artifacts and affected A7 candidates removed. Whole scrubs text/SSML/plan/feedback,
  cancels initial generation; selected-level running generation409. Expired retained
  text deletable by known ID, no audio revival. Hash change closes all bound sessions;
  A7 all-level edits target remaining levels. A2 removes B7-owned directories too.
  UI B7 page, A5/A7 links, explicit scope/confirmation/manual pinned retry, binding-
  scoped caches, source lists preserved. frontend Docker COPY and allowlist required.
- C1 health/ready; C2 cached reasons/kill switches, UNKNOWN without recent evidence,
  no AWS polling. C5 source/config/model/prompt hashes/provenance; A7 adds revision
  refs. Revision strategy three_role_replace_existing_chunk, all_levels_supported,
  teacher-feedback-aware-v1, max2alignment/max2final-output corrections,
  prompt chunk-revision-v3; checkpoints
  bind configuration. Human approval remains external; all results DRAFT.

## Important paths

- backend/src/upraise_ai/{api,schemas,workflows}/ contracts; api/generated_content.py/
  workflows/lesson_deletion.py B7; backend/docs/ contracts. services/lesson_*.py/v3/
  math/speech, aligned_{models,pipeline,responses}.py A3v2,
  revision_{models,pipeline,validation}.py A7, lesson_replacement.py publication/
  cleanup/consolidation. storage/{lessons,sessions,courses}.py persistence;
  workflows/{sessions,interrupts}.py/guards/session_jwt.py runtime;
  services/pdf/ A1, services/versions.py C5.
- frontend/{ingestion_workspace,chunk_revision,revision_progress,generated_deletion,learning,
  learning_session,learning_interrupt,lesson_audio}.py; tests/: AppTest/fakeHTTP.
  frontend/README.md UI commands; backend/{pyproject.toml,uv.lock,Dockerfile,
  math_runtime/} builds. Chunk uniqueness(job_id,level_key,index), legacy level_key=''.
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
  isolated suite; empty -k breaks PS. Validation uses labeled disposable PG/Qdrant,
  fake providers, no live volumes/credentials; image upraise-b4-validation.
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

Sequential ingestion/lesson workers and A3v2 variants. No archive read API or expired
audio restoration. DOCX/CSV, A7 idempotency/structural revision and mid-session level
switching future. Gateway/Moodle callbacks/model access, HA/backups/erasure/orphans/
cross-host storage unverified. Fake-provider agent tests; user starts live generation.
Grounding backlog4, independent post-edit A3 review deferred, A7 alignment retained.
Semantic findings: level-content-assessment.md/beginner-retry-diagnosis.md. Failed
A3 checkpoints retained; missing responses unrecoverable. WAV integrity is structural,
human review required. Legacy consolidation preserves content.
Historical a21ee200 alignment/55a9d13a finalization failures lacked exact retained
findings/output; v11/v12 added recovery and diagnostics. Helper reports:
a7-alignment-failure-diagnosis.md/a7-output-failure-diagnosis.md. Do not auto-resubmit.

## Recent decisions

- A7 selected-level default with optional all-level teaching-unit edit, publishing
  all targets together on success and deleting
  superseded content. Processing IDs serve progress/audit without catalog versions.
- Source/course deletion has independent retained-text lifecycle; A6 remains
  temporary-output receipt/cancel. Changed lesson hash invalidates runtime sessions.
- B7 is the user-approved B-series generated lesson/level deletion extension;
  B6 keeps its reserved telemetry purpose. Source documents remain reusable.

## User notes (preserve verbatim)

## Last verified

2026-10-13 B7: API/workflow/storage/audit, A7 publication fence, A2/A6/TTL cleanup,
C5/docs, frontend page/cache/navigation and Docker packaging verified. 888 full
isolated backend/108frontend tests pass, including scope/ownership/idempotence,
503 recovery, expired archive, source/plan/sibling preservation, affected/unrelated
revisions, remaining-level all-level A7, session closure, source cascade and UI
confirmation/pinned retry/navigation/auth reset. Ruff/diff/build pass; additive
migration deployed. API/UI/workers healthy, matching backend fingerprints; B7 UI
imports and4packaged source hashes match. 16GET/read-only capability checks pass,
no live DELETE/provider call. Live126retained text rows across8roots, plans, current
60WAV/manifest hashes and8source vector counts unchanged. No live B7 operations.
Pipeline lesson-v3-a3-12/promptv3 unchanged; deletion generated-content-deletion-v1.
Evidence: codex_helper/b7-*. Prior A7 recovery regression remains covered.

Prior helpers: a7-validation-* (v11 alignment), a7-all-levels-*/a7-replacement-*
(publication/concurrency/session/cleanup/consolidation); turing-content-verification.md/
level-content-assessment.md (unresolved semantic findings). EN/IT25voice catalogue
checked;20endpoint PDF predates A7/A4. Older lifecycle/runtime: course-deletion-*,
learning-session-verification.md/alignment-recovery-fix.md under codex_helper/.
Historical checks do not imply availability or human approval.
