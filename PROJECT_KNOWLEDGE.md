# Project Knowledge

## Purpose and current state

UPRAISE WP7 implements A1-A8/A3v2,B1/B2/B4/B5/B7,C1/C2/C3/C5;B3/B6/C4 reserved.
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
- Teaching V2 is distinct from frontend2/A3v2. teaching_version=v2 opts in;
  absent/v1 preserves original models/prompts/converter/fingerprint. UI defaults V1.
  Isolated services/teaching_v2/; docs/teaching-v2.md and reverse-checked
  codex_helper/teaching-v2-runtime.patch preserve earlier chat changes.
  V2.8 concept author/exact selector checks coverage/quotes, restores original
  math/tables and excludes incidental fragments. After2bad quote attempts retain
  original primary chunk, exclude invalid optional context; no guessed repairs.
  Same-doc/version/attempt context6refs/12000chars clarifies terms, not scope;
  bind after model creation, cache/job. Empty primary never becomes another topic.
  Typed factual/omission/language/citation findings and local exact reference/
  unit checks; absent-source claims use -1/empty quote. Broad review2quote attempts
  then indexed original spans. Independent verification can downgrade source/
  presentation-only accusations. Host-bound entailment checks EVERY paragraph
  against its allowed material; no model-retyped quotes/indices. Numeric question
  givens bounded; ambiguous labels attributed as printed. No document hardcoding.
  Max2factual corrections, final edit by reviewer. User approved failed NEW sections
  copying exact primary excerpts with teacher-editing DRAFT flag; failed adaptation
  can retain reviewed canonical text flagged for level edit. Preserve IDs/language/
  references/size; refine common plan, never truncate. Invalid evidence/provider/
  language still fail closed. A7 remains strict, no fallback discarding teacher edits.
  Same-level canonical reuse avoids rewrite/V1 markup repair; translations adapt.
  Policy/recovery metadata invalidate checkpoints. Source/style gaps are warnings.
  Separate strict spoken meaning/language/naturalness gate before Polly, script
  cache/published SSML. AI gates remain fallible: real scripts still invent wrong
  interpretations (assessment below). No V1 fallback/automatic replacement.
  Review script/audio/Stop; release on navigation. No actual listening verified.
  Owner-only staging/A4 safe failure details; A6/TTL/A2 erase bounded diagnostics,
  never raw provider output/logs/audit. Extraction itself is not repaired.
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
Source input untrusted; parsing success does not establish source factual accuracy.
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
Oct19 user approved written-only Bedrock debugging for this Random Graphs course's
windows/context/drafts/findings via both configured models, no audio/replacement.
Earlier approval denials resolved; never auto-submit jobs. UI desktop/mobile checked;
no direct listening or Markdown/LaTeX typesetter/runtime npm.
V1 Random Graphs has matrix/conditioning/raw-escape SSML defects. Completed V2.8
improves wording but misreads XF (part7), equates fixed/expected edges (8), assumes
normal-table p-values (21), adds unreviewed narration.12/23fallback flags,finalpart
empty. Editorial V1~4/10,V2.8~6/10; spoken wording3->8,reliability4->4.
Continuity gap confirmed:12/23new-lesson openings; part9 missing formula appears
in10. Written author has source context but no taught-history/outline; narration
gets only current text/language. Shared unit plan is alignment, not pedagogy.
Evidence codex_helper/teaching-v28-quality-assessment/; scripts/audio-integrity only.

## Recent decisions

- Teacher review lists durable text independently of audio. User approved root
  course_archive/ retention so saved packages stay playable/revisable beyond24h.

## User notes (preserve verbatim)

## Last verified

2026-10-19 assessed completed V2.8 c1d0a07c:23parts/23.84min,1973written/
4376spoken words,12fallback flags. Same source/attempt/models as V1 99d9058e
(32parts/36.79min); Joanna V1 acd83efc corroborates. Read23V2text/scripts,32latestV1
texts/matched scripts and sources.23WAV hashes/durations match,mono16bit16kHz.
No listening/provider calls/code or course edits. Assessment path above; index refreshed.
Follow-up checked aligned make_plan, V2 models/context/speech inputs and repeated
openings/formula transition; course continuity absent. Report/index refreshed only.
Earlier same-day deployment:17hashes/service across4backend services,2V1results
unchanged,all8healthy.341selected regressions,135+1V2,Ruff/build/diff/rollback checks
passed. Written debug pilot23windows16normal/7fallback was a separate run;
actual generation differs. Evidence codex_helper/teaching-v28-{written-verification,
runtime-verification}.json,final-report.md. Fake-provider success does not establish
real semantic correctness. No automatic regeneration/submission.
Historical V1:codex_helper/random-graphs-comparison-20261016/. Diagnostics may
TTL-clean; prior scoped QA:38DOM/15bridge/Edge,135B1/B4/B5,archive117.
