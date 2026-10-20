# Project Knowledge

## Purpose and current state

UPRAISE WP7: A1-A8/A3v2,B1/B2/B4/B5/B7,C1/C2/C3/C5;B3/B6/C4 reserved.
PG DRAFT text/plans and Qdrant sources persist; A5 staging TTL24h, completed packages
saved in course_archive/. Moodle owns approval. No stage/commit without request.
../Uprise/v2 reference-only; semantic findings/evidence in codex_helper/.
frontend2/ is the client UI on localhost:8502 via stdlib HTTP bridge; frontend/
remains a separate Streamlit testbench/player. C3 schema exists.

## Architecture and data flow

- frontend2 bridge: bounded routes/headers,TLS/Nonce/Host/Origin,OAuth/mTLS;
  JWT/retries tab RAM, no browser storage/direct AWS/autoPOST/A6. WAV/hash/session/
  offset and B5 late fences. Uncertain A7 needs new request. Levels1+,default all.
  B4 keeps visible Q&A per interruption, Listen/Pause/Resume/Stop/Replay/Ask again.
  Continue clears UI/audio; next interruption blank. Full backend chat RAM lasts
  until B5/end/expiry/revocation/restart; distinct last2retry/audio cache. Models
  get last4complete pairs,2000chars/message, no blocked/player-only messages;
  question UUID/stable retry identity prevents duplicate pairs.
- Teaching versions are distinct from frontend2/A3v2. teaching_version=v1(default)
  preserves original teacher/prompts/MathJax. Isolated teaching_v2/: exact source
  selection, independent material/entailment review, max2corrections, flagged
  source-excerpt DRAFT recovery, canonical reuse and separate spoken review/cache.
  Warnings never permit changed facts; A7 strict. docs/teaching-v2.md; rollback
  snapshots/patches under codex_helper/.
- Opt-in V3: services/teaching_v3/, docs/teaching-v3.md. Document-order plan,
  shared context/exact same-page equation definitions. Adjacent continuations
  merge within24k teaching chars/full payload bound; metadata excluded. Primary
  coverage differs from supporting citations; split complete paragraphs. All
  boundaries/levels reviewed before TTS. Max2corrections then flagged PRIMARY-
  source DRAFT recovery. Narrator sees actual prior writing/navigation, no future
  source; questions read, not answered. Preserve table columns/cases/leading zeroes;
  current-written entailment/reverse coverage/source-equal decimal normalization.
  Explicit math consequences allowed; no guessed conditions or waived omissions.
  Source typos not foreign prose. Narrow review edits; caches bind context/policy/
  models/output, changed neighbours invalidate; full-course review always. Conflicts
  bind actual assertions/explicit subjects; real conflicts block. JSON format/
  token-limit retries; host index faults fatal. Planning announces workload first.
  A7 inheritsv3/target-only/strict. V1/V2,TTL/A6/A2 caches/content-free logs preserved.
- Start learning badges read A8 teaching_version and stored per-level teaching_policy.
  Historical omitted request version is V1 by contract; V2.8 needs consistent part/
  level policy, otherwise V2. Missing/unsupported UI provenance shows unknown.
  These are independent of a3-v2 API format; no generation/backfill is needed.
- Nav: Upload / Generate / Review / Start learning. Review reads durable PG text,
  levels/parts/source evidence; tab-only feedback and A7 selection persist. Expired
  text readable; A7 needs completed temporary/archived output. Ownership/course/
  deletion/publication fences; unpublished candidates hidden, content-free audit.
- Archive: ignored course_archive/, optional UPRAISE_LESSON_ARCHIVE_DIR on API/
  workers. Manifest/WAV/hash verified; A5/A7/A8/B1 survive staging TTL. A7/B7 removes
  superseded packages; B7/A2 erases owner archives, A6 withdraws delivery only.
  Startup rescues intact expired packages; no audio regeneration/chat archive.
  artifact_job_id/hash/manifest selects current owner paths.
- A1 Docling/vision/math PDF, CsvProcessor, WordProcessor/LibreOffice fidelity
  fallback -> ordered Titan/Qdrant chunks; active-attempt fences, ingestion-v1.6.
  CSV UTF8/BOM headers, comma/semicolon/tab, complete rows; Word block refs, no
  invented PDF pages. PDF quality fingerprints/contracts remain.
- A2 deletes dependent PG/plans/artifacts/feedback; writer/admission fences and
  durable503 recovery. IDs/audit remain; fresh A1 allowed.
- A3v1 ordered5page windows/V3 roles/checked translation/speech. A3v2 shares a
  canonical plan/content units across levels, distinct chunk/audio IDs. Text/plans
  precede TTS; target200/max500words, max2 shared refinements. v2.5/promptv6 bounds
  ID-aware recovery, rejects ambiguous/truncated output; approved drafts skip edits.
- A3v2 levels:1-8 configured names, omitted/null=all, config order. Plan level if
  selected, else first selected. Fingerprint ignores ordering, conflicts on selection.
- CSV/Word pages[]/page_count0; source_blocks/CSV row_ranges/Word refs. CSV header
  row1, multiline cells one record. A3/A7/B4 preserve refs, omit row/block labels
  from speech; PDF unchanged. Extraction does not establish accuracy.
- A4 level/total counts; A5 bundle/learner_level slice shares full integrity hash;
  A8 expands before pagination. A6 fences writers; retained text/plans independent.
- A7 source-pinned chunk/content_unit_id edits, optional levels;2corrections,
  cache survives TTS outage. Atomic PG/manifest/hash/WAV; stale409, unrelated edits
  merge, artifact_job_id/current-result.json recovery; success refreshes TTL.
  ChildA6 protects current;rootA6/A2 removes output. UI cross-level off/manual;
  failures never audited, erased by A6/TTL/A2/replacement. docs/a7.md.
- B1 binds job/context/hash/level. Local DRAFT preview or signed OAuth/mTLS
  approval/consent; RAM30min capped by token/lesson, clears on restart/hash change.
  B2 supplied score>.5 HIGH_COGNITIVE_LOAD else PROCEED;consent,no measurement.
  B4 source-scoped PG/Qdrant LangGraph:text/WAV-STT->answer/SSML/WAV/visemes;
  TTS outage retains text,grounding=null/GROUNDING_NOT_VERIFIED. B5 late fences,
  RAM clear/idempotent content-free audit30min. Contracts:docs/b1-b5.
- B7 DELETE /v1/lesson-jobs/{id}?learner_level;lessons:write; preserves sources.
  Partial/whole deletion, tombstones/503 recovery and manual retry: docs/b7.md.
- C1 health/ready; C2 cached evidence/no AWS polling. C3 admin:operations controls,
  enabled=true disables; fail-closed queued pause, audit outbox. docs/c3.md.
  C5 hashes/provenance/checkpoints. Human approval external; all results DRAFT.

## Important paths

- backend/src/upraise_ai/{api,schemas,workflows}/ contracts; backend/docs/ details.
  services/{lesson_*,aligned_*,revision_*}.py lifecycle; pdf/,csv_processor.py,
  word_processor.py ingestion; storage/ persistence; guards/session_jwt.py sessions;
  workflows/controls.py C3; schemas/versions.py C5. Build:pyproject.toml/uv.lock/
  Dockerfile/math_runtime/. Chunk uniqueness(job_id,level_key,index).
- frontend/ Streamlit/AppTest; frontend2/{app,api,ui}.js,index.html,styles.css,
  serve.py bridge,tests/{test_server.py,ui.test.cjs}. README auth/run/Docker allowlist.
- Review: {schemas,workflows}/lesson_review.py, storage/lessons.py, docs/lesson-review.md.
  Archive: storage/lesson_archive.py, services/lesson_replacement.py,
  workflows/archive_lessons.py, docs/lesson-archive.md. Matching integration tests.
- codex_helper/: ignored helpers/evidence; FUTURE_UPDATES.md ignored backlog.
  turing-content-verification.md/level-content-assessment.md: semantic findings.
  teaching-v28-quality-assessment/assessment.md: actual completed V2.8 vs V1,
  scored rubric, per-part source/script evidence and audio integrity checks.
  teaching-v3-comparison/assessment.md: latest same-PDF V1/V2.8/V3 quality/timing.

## Commands

- Docker Desktop Linux; API8000/Streamlit8501/frontend2 8502. Build:
  docker compose build backend frontend frontend2. Migrate:
  docker compose run --rm --no-deps migrate. Refresh changed shared backend services:
  docker compose --profile tools up -d --no-deps --wait --wait-timeout 180 backend worker lesson-worker lesson-delivery
- Drain jobs before pipeline/config changes; restart clears RAM sessions. Never
  reset live volumes/re-ingest sources for A3. Runtime pipeline default lesson-v3-a3-12.
- codex_helper/validate-a3v2.ps1 -Selection 'not codex_nonexistent_sentinel': full
  suite; empty -k breaks PS. Disposable PG/Qdrant/fake providers, no live data;
  image upraise-word-validation (codex_helper/word-ingestion/Validation.Dockerfile).
- UI run/auth: frontend{,2}/README.md; local python frontend2/serve.py (3.10+stdlib).
- UI tests: python -m unittest discover -s frontend2/tests -p test_server.py -v;
  npm --prefix frontend2 ci --ignore-scripts; npm --prefix frontend2 test.
- Ruff backend config backend/pyproject.toml; frontend scoped E4,E7,E9,F with test
  E402 excluded. git diff --check. One-off logs/evidence under codex_helper/.

## Dependencies and environment

Python3.12/uv,Docling/OCR/CPU Torch,PG17/Qdrant,Streamlit/httpx,LangGraph/Transcribe;
EU Bedrock GPTOSS/Qwen,Nova/Titan1024D,eu-central-1. Word LibreOffice/fonts180s.
Polly neural16kHz mono16bit,PCM visemes,SSML2800billed/5800total. Local Python3.10
cannot run backend: use Docker. PS5.1 JSON explicitly UTF8; pipe single-quoted here-strings.

## Conventions and invariants

UPRAISE_LESSON_LEVEL_PROFILES ordered JSON1-8profiles; plan level must match,
batch4/max8. Explicit level array survives PG JSONB ordering. Chunk identity
(job_id,chunk_id), index0-based; content_unit_id links levels. Source content_hash
opaque/source_hash realSHA256; PDF100MB/500pages, CSV100MB/100krows/256columns.
Sources untrusted; parsing does not establish accuracy.
Logs/audit omit text/scores/audio/prompts/private provider errors; optional local
diagnostics removed by A6/TTL or successful replacement. Ownership/source/leases/
artifact locks fence publishing; corrupt503/pending409/expired410. Attempts3/max12h,
supervised killable children; progress does not reset deadline. Scopes documents:
read/write, lessons:write, jobs:read/write, sessions:write; inaccessible404/conflict409/
invalid422. C3 uses admin:operations;default local scopes exclude admin.
Missing production auth config closes ingress; no Redis/session renewal.

## Known limitations and next work

Sequential workers/variants; missing audio needs synthesis. Archive noTTL; expired
unsaved text read-only. Word fidelity needs review. NonUTF8/headerless CSV/URLs,
A7 idempotency/structural edits and mid-session level switching are future work.
Gateway/Moodle/HA/backups/erasure/orphans unverified; V1 final review/grounding deferred.
User authorized V3 repairs and intermediate same-PDF Bedrock/Polly test generation/
retries; preserve old courses, no unsolicited submissions. UI desktop/mobile
checked; no listening assessment
or Markdown/LaTeX typesetter/runtime npm. Earlier Word V1/V2.8:4/10 vs6/10;
V2.8 has12/23fallback flags and repeated openings/split math/added assumptions.
V3 does not repair extraction. Evidence codex_helper/teaching-v28-quality-assessment/.

## Recent decisions

- Teacher review lists durable text independently of audio. User approved root
  course_archive/ retention beyond24h.

## User notes (preserve verbatim)

## Last verified

2026-10-20: catalog schema/storage/workflow, frontend2/app.js/styles.css and docs
checked for generation badges. 43 UI,27 A8 integration,15 bridge tests and Ruff/
build passed. Backend API/frontend2 deployed healthy; served assets match source.
All8 existing course result hashes preserved; actual A8/UI labels verified for
V1943e1d29,V2.8 26225185,V3b8cec526. Evidence codex_helper/course-version-labels/.
2026-10-19 assessment: latest V3b8cec526 is9parts/11.54min/Matthew,5fallbacks,
21.11min generation; same-PDF V1/V2.8 are13/9parts,17.13/14.92min audio,
6.80/14.21min generation. All attempt1; same source/models/options,V2 usesAmy.
All31 WAV/text/SSML verified; no listening. Overall4.5/6/6.5; V3 improves math
fidelity but still literal bars/spoken citations,question-only part6,duplicated
figure/repeated definitions. codex_helper/teaching-v3-comparison/ has evidence.
Prior V3 repair:88tests/Ruff/build/rollback passed; old results preserved.
e2014342 repaired test DONEattempt1,5parts/8.14min/Joanna,all5fallback flags.
PDF79fd4c5e-b3ef-426e-bf47-78ae09a2ef0e. eae69375 failed startup timeout then
invented-subject conflict; interim5c733517 wrapped unreadable output as fatal.
Explicit-subject assertion binding and retryable complete JSON/paragraph review
fixed both. Details codex_helper/v3-inconsistency-repair/. UI defaultsV1.
