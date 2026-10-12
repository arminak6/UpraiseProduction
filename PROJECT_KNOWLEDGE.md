# Project Knowledge

## Purpose and current state

UPRAISE WP7 implements A1-A8, A3 v2, B1/B2/B4/B5 and C1/C2/C5. Streamlit is an
HTTP testbench/player. PostgreSQL retains DRAFT text/SSML; Qdrant holds sources;
A5 JSON/WAV expires independently (24h). Middleware/Moodle owns human approval.
Live A3v2 job6c8d3bc5 completed once:15 units/level,45 PG chunks/WAVs verified.
Full text review: same broad topics, partial coverage and uneven level separation.
B3/B6/C3/C4 remain reserved. Original ../Uprise/v2 is reference-only.

## Architecture and decisions

- A1 Docling/vision/math extraction once -> ordered Qdrant chunks; active attempt
  prevents mixing source revisions. Returns document_id and job_id; A2 list/delete.
- A3 v1 POST /v1/lesson-jobs: one requested level; ordered5page windows -> original
  V3 generator/reviewer/editor -> checked translation if A1/A3 languages differ ->
  ~200word chunks -> PG text before Polly, exact SSML before TTS. docs/a3.md.
- A3 v2 POST /v2/lesson-jobs: all configured levels, initially three. Canonical
  material generated once, segmented into one shared plan; original three-role
  generation/review/edit adapts each level. Approved drafts retained; negative
  draft review goes to editor and remains in DRAFT reports for human approval.
  Additional blocking post-edit material review deferred in FUTURE_UPDATES.md.
  Exact unit IDs/order/pages/size enforced; original V3 math delimiter/speech
  recovery retained, without formula-string equality. Semantic quality depends
  on draft source review/editor and human assessment, not an extra final judge.
  A1 extraction unchanged. Complete JSON/string recovery handles safe escape errors;
  ambiguous/duplicate/truncated data fail closed, bounded token retries and
  content-free stage/reason logs distinguish provider failures. Translation
  preserves units. Target200/max500 defaults; bounded
  shortening then at most two common plan refinements, never level-only splits.
  One complete bundle/job; same count/index/content_unit_id/source scope, distinct
  chunk_id/text/WAV/duration. All texts/plan saved before speech. Semantic quality
  still needs human review. Old courses unchanged. docs/a3-v2.md.
- A4 v2 counts are per level plus total. A5 bundle or ?learner_level=beginner,
  one root integrity hash; audio IDs unique across levels. A8 expands levels before
  pagination. A6 receipt/cancel/TTL fences writers, removes all temporary artifacts;
  durable PG text/SSML/shared plans/audit survive. docs/a5.md/a6.md/a8.md.
- A7 teacher_review + job/chunk -> exact PG/pinned sources -> three roles/translation/
  TTS -> new complete standalone revision of selected level. Parent unchanged;
  stable count/order/unit IDs, v2 material/max-size checked again. Structural
  changes need new A3 plan. Request idempotency explicitly deferred. docs/a7.md.
- B1 exact job/context/hash/approval JWT plus selected v2 learner_level; fixed30min
  RAM-only max10000, audit before visibility, no capabilities. Local DRAFT preview.
  Inherits language/voice/profile/source scope and only selected variant's chunks.
  Service OAuth/mTLS and separate session JWT are both required in production.
- B2 chunk-end: lesson_id/chunk_id/cognitive_load_score finite0..1; telemetry consent,
  >.5 HIGH_COGNITIVE_LOAD else PROCEED. No consent discards score. RAM-only score/
  index, content-free ID/version audit; test-v1 threshold, fusion/B6 absent.
- B4 request-local LangGraph classify -> scoped retrieval/support -> respond.
  RAG paired document/attempt filtering and source-language query rewrite; support
  uses selected PG chunk. Questions consent/JWT; text/WAV-STT -> text/SSML/WAVbase64/
  visemes or playback control. TTS failure preserves text. No learner disk/tracing.
  User deferred grounding: grounded=null, GROUNDING_NOT_VERIFIED; safety/language
  remain. One turn/session, max4 concurrent,32MiB/100-message cache, WAV60s/6MB.
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
- frontend/{app,learning,learning_interrupt,session_start,chunk_revision}.py: HTTP UI;
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
- Earlier real-course AWS egress test auto-review rejected: use invented data/fake
  providers. User can initiate real curl/UI generation tests. Grounding backlog4.
- Failed A3 v2 jobs retain checkpoints but no published PG/audio. Old rejected
  responses were not captured, so exact causes cannot be proven retrospectively.
  v2.4 uses original three-role flow; independent post-edit A3 verification is
  deferred. Review findings remain visible, without claiming final/human approval.
  A7 retains its revision-specific alignment review. Completed job6c8d3bc5 has
  literal Unicode/double-math escapes and incorrect SSML such as 'L empty'; valid
  WAV generation does not prove teaching/speech quality. All45 texts reviewed:
  same broad topic in15 triples, scope gaps in chunks1/11/12 (1-based), advanced3
  adds .05 threshold; difficulty often cosmetic. Intermediate5/6 contain review
  JSON; advanced5 has trailing .replace code. Report level-content-assessment.md
  under codex_helper. Review/escape cleanup needed; failed jobs need new requests.

## User notes (preserve verbatim)

## Last verified

2026-10-12 read-only full text assessment of job6c8d3bc5-a825-4a18-b814-9a61b86a6b07:
PG retained45 texts/15 canonical units; every ID/section/source matches its plan.
All15 triples share broad topics, but material gaps/additions and inconsistent
difficulty remain (Limits above). All45 texts read, not only prior samples;
no independent full-PDF factual certification/audio listening. Evidence:
codex_helper/{export-level-content-audit.py,level-content-audit-data.json,
assess-level-content.py,level-content-assessment.md}. Docker Desktop was stopped;
started only PG for temporary read-only helper, then stopped PG again. Generation
workers remained stopped; no runtime edits/provider calls. Project services stopped.
Current config/profiles/aligned schemas/models/pipeline rechecked.

2026-10-10 three-role update deployed; extra A3 review deferred, A7 unchanged.
193 offline/30 disposable integration tests with fake providers and
Ruff/Compose/diff/build passed. API/worker C5: aligned-lessons-v2.4-1452bdbd12b0,
aligned-teaching-v5, review original-three-role-v1/post_edit_material_review=deferred;
configuration sha256:2351c1daea507d8509a9a098d961299c7a2805b45dcc14b9a85e8952ace32bf1.
Evidence alignment-three-role-* and verify-alignment-three-role.py in codex_helper.
Live user retest6c8d3bc5: DONE/attempt1,16m46s, English/Joanna,15 chunks/level.
45PG texts/SSML match A5;45 WAV hashes/formats/durations/visemes passed.
DRAFT164 source/draft findings are not164 confirmed final errors. Earlier samples
found Unicode escapes4 beginner chunks and doubled math/SSML 'empty'8 chunks.
Evidence check-aligned-success.py/ aligned-success-{check.json,assessment.md}.
Source79fd4c5e-b3ef-426e-bf47-78ae09a2ef0e: DONE/en/31pages/53chunks, attempt
c90d99b1-1cca-4b11-888e-73868e302f65, pdf-ingestion-v1.4-10273fc36174; PDF hash matched.
Historical483b3ea3/c4be12d9/0ac79061/d8f11792 FAILED, unpublished; exact candidates
unavailable. Earlier authorized reset deleted older lessons, retained sources/audit.
Reset helpers must never rerun without user request. No real AWS test by agent.
