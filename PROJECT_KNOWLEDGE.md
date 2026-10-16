# Project Knowledge

## Purpose and current state

UPRAISE WP7 implements A1-A8/A3v2,B1/B2/B4/B5/B7,C1/C2/C3/C5;B3/B6/C4 reserved.
C3 schema tables exist; API/workers refreshed with current source on 2026-10-15.
Streamlit is an HTTP testbench/player. PG DRAFT text/plans and Qdrant sources persist;
temporary A5 files have24hTTL; Compose saves completed packages in course_archive/.
Moodle owns human approval. Do not stage/commit
without request. ../Uprise/v2 is reference-only. Semantic source/math/review
defects and historical course deletion evidence are documented under codex_helper.
frontend2/ is the approved client UI, connected through a stdlib Python HTTP bridge.
Its Docker service runs on localhost:8502; original frontend/backend remain separate.

## Architecture and data flow

- Streamlit HTTP testbench: A1-A8/v1/v2, B1/B4/B5, verified deferred audio.
  Retry keys survive navigation; auth/context clears caches. EN/IT25neural voices.
- frontend2 uses real A1-A8/B1/B4/B5 APIs through a same-origin, route/header-limited
  stdlib bridge. Nonce/Host/Origin checks, verified TLS/no redirects and server-side
  OAuth/mTLS. Learner JWT/retries stay in tab RAM; refresh loses local state.
  WAV SHA256/result-hash/session/expiry checks, exact B4 offsets and B5 late-response
  fences. No browser storage/seed data/direct DB/AWS, automatic POST or A6 receipt.
  Uncertain A7 requires explicit new-request preparation. Custom levels come from
  A5; generation selects1+ configured levels, default all, retained while browsing.
- frontend2 top navigation: Upload material / Generate course / Review courses /
  Start learning. Review uses GET /v1/lesson-reviews[/{job_id}] for durable PG text,
  one course per entry, ordered levels/parts, full text/sources/review evidence and
  tab-only feedback per part. Inline A7 retains selected course/level/part and polls
  safely. Expired/received output and incomplete root text remain readable, with
  revision disabled unless a completed course has temporary or archived output.
  Review reads enforce jobs:read/client/course/source/deletion fences, coherent
  publication locks and content-free access audit; A7 candidates remain hidden.
- Persistent archive: optional UPRAISE_LESSON_ARCHIVE_DIR; Compose mounts ignored
  root course_archive/ at /data/course_archive on API/ingestion/lesson workers.
  Verified manifest/WAV-only copies precede DB publication; result.persistent_output
  and the existing artifact/hash pointer select immutable owner/artifact/hash paths.
  TTL removes staging only; saved A5/A7/A8/B1 remain available with no output expiry.
  B1 sessions stay bounded. A7/B7 replace packages and remove superseded versions;
  whole B7/A2 removes owner archives. A6 receipt withdraws delivery but keeps archive.
  Startup copies intact old packages (including expired-but-present files), never
  restores missing audio or calls AI/TTS. Archive storage outages retry from caches.
- A1 PDF uses Docling/vision/math, CSV uses CsvProcessor, Word uses WordProcessor
  with LibreOffice fidelity fallback. Ordered Titan/Qdrant chunks; active-attempt
  fences, document-ingestion-v1.6. CSV UTF8/BOM header-based comma/semicolon/tab,
  complete rows only; limits100MB/100krows/256columns. Word native picture/block
  references never fabricate PDF pages. PDF quality fingerprints/contracts remain.
- A2 owner/source/course deletion removes dependent PG/plans/artifacts/feedback
  across levels/languages/statuses, with admission/writer fences and durable503
  recovery. Minimal IDs/audit remain; fresh A1 is allowed after deletion. Original
  ingestion files retain an independent artifact policy; indexed content is erased.
- A3v1 uses ordered5page windows and V3 roles, checked translation, then speech.
  A3v2 shares a canonical plan/ordered content units across levels, with distinct
  chunk/audio IDs. All text/plans precede TTS; target200/max500words and at most
  two shared refinements. v2.5/promptv6 uses bounded ID-aware recovery and rejects
  ambiguous/truncated output. Approved drafts skip editing; independent post-edit
  review is deferred. Source/math quality still needs human review.
- A3v2 optional levels: 1-8 unique configured names; omitted/null means all.
  Snapshot selected profiles in config order; planning uses the configured plan
  level if selected, otherwise the first selected. Selection is fingerprinted:
  reordered equivalent selection reuses the job; changed selection conflicts.
- CSV/Word pages[]/page_count0; source_blocks plus CSV row_ranges or Word refs.
  CSV header is row1; multiline cells remain one record. A3/A7/B4 preserve source
  references, and row/block citation labels are omitted from narration. PDF shape
  stays unchanged; extraction never establishes factual accuracy.
- A4 counts per level/total; A5 whole bundle or learner_level slice with one full
  integrity hash; A8 expands levels before pagination. A6 temporary receipt/cancel/
  TTL fences writers; retained A3 text/plans have independent lifecycle.
- A7 source/PG-pinned roles/translation/TTS; selected chunk or optional shared
  content_unit_id across remaining levels. Strict revision_models final checks and
  revision_validation alignment: max2corrections each; accepted cache survives TTS
  outage. Exhaustion terminal,outages retryable;new POST for old failures. Approved
  drafts skip editing; corrected drafts unapproved. docs/a7.md.
- A7 atomic PG/manifest/hash/WAV replacement preserves IDs; failure/cancel preserves
  current. Stale target409,unrelated edits merge. artifact_job_id/current-result.json;
  replacement_cleanup_jobs recovers obsolete/candidate artifacts. Success refreshes
  TTL;A8 hides processing IDs;A5 aliases current level. ChildA6 protects current;
  rootA6/A2 removes output. Startup consolidation never revives expired audio.
- A7 UI checkbox defaults off,polls5s,pauses on errors/stops terminal;success reloads
  same lesson/level/part. No auto POST. Safe failure details are temporary,excluded
  from logs/audit and removed by A6/TTL/A2 or successful replacement.
- B1 binds playback job/context/hash/level. Local DRAFT preview or OAuth/mTLS signed
  approval/consent;RAM30min capped by token/lesson,cleared on restart/hash change.
  B2 supplied score>.5 HIGH_COGNITIVE_LOAD else PROCEED;consent,no measurement.
  B4 source-scoped PG/Qdrant LangGraph;text or WAV-STT -> answer/SSML/WAV/visemes;
  TTS outage keeps text,grounding=null/GROUNDING_NOT_VERIFIED. B5 fences late replies,
  clears RAM,idempotent content-free audit30min. docs/b1-b5 contracts are authoritative.
- B7 DELETE /v1/lesson-jobs/{id}?learner_level;lessons:write. Omit/last level removes
  whole lesson, sources preserved; tombstones/fences/recoverable503/repeat204.
  Partial atomic update preserves siblings/plan/expiry. Running-level409; expired
  text deletable, no audio revival. UI pinned confirmation/manual retry; docs/b7.md.
- C1 health/ready; C2 cached provider evidence (UNKNOWN without recent calls), no
  AWS polling. C3 global/course controls: admin:operations, enabled=true disables,
  deployment disables prevail, admission fails closed, queued jobs pause without
  consuming attempts. Atomic audit outbox; local admin opt-in; docs/c3.md.
  Disabled B2 discards scores/PROCEED; in-flight AI not hard-cancelled.
  C5 hashes/provenance/checkpoints. Human approval external; all results DRAFT.

## Important paths

- backend/src/upraise_ai/{api,schemas,workflows}/ contracts; backend/docs/ details.
  services/{lesson_*,aligned_*,revision_*,lesson_replacement}.py generation/lifecycle;
  services/pdf/, csv_processor.py, word_processor.py ingestion; storage/ persistence;
  guards/session_jwt.py sessions; workflows/controls.py C3; schemas/versions.py C5.
- frontend/ Streamlit authoring/learning modules; tests/ AppTest/fakeHTTP;
  frontend/README.md commands. backend/{pyproject.toml,uv.lock,Dockerfile,math_runtime/}
  builds. Chunk uniqueness(job_id,level_key,index); legacy level_key=''.
- frontend2/{app,api,ui}.js + index.html/styles.css; serve.py HTTP bridge,
  tests/{test_server.py,ui.test.cjs}; README.md auth/run/verification. Docker allowlist.
- Teacher review: backend/{schemas,workflows}/lesson_review.py under src/upraise_ai,
  storage/lessons.py list_reviewable; api/authoring/lessons.py routes;
  backend/docs/lesson-review.md and tests/test_lesson_review_integration.py.
- Archive: storage/lesson_archive.py, services/lesson_replacement.py artifact_directory,
  workflows/archive_lessons.py backfill, backend/docs/lesson-archive.md;
  tests/test_lesson_archive_integration.py. Keep mount/config on API and all workers.
- codex_helper/: ignored helpers/evidence; FUTURE_UPDATES.md ignored backlog.
  turing-content-verification.md/level-content-assessment.md: semantic findings.

## Commands

- Docker Desktop Linux; API8000/UI8501. docker compose config --quiet.
- Build: docker compose build backend frontend. Additive schema setup:
  docker compose run --rm --no-deps migrate. Refresh API/UI/workers together:
  docker compose --profile tools up -d --no-deps --wait --wait-timeout 180 backend frontend worker lesson-worker lesson-delivery
- Drain jobs before pipeline/config changes; restart clears RAM sessions. Never
  reset live volumes/re-ingest sources for A3. Runtime pipeline default lesson-v3-a3-12.
- codex_helper/validate-a3v2.ps1 -Selection 'not codex_nonexistent_sentinel': full
  suite; empty -k breaks PS. Disposable PG/Qdrant/fake providers, no live data;
  image upraise-word-validation (codex_helper/word-ingestion/Validation.Dockerfile).
- Frontend: docker compose run --rm --no-deps -v ./frontend:/workspace:ro frontend python -m unittest discover -s /workspace/tests -q
- Client UI: python frontend2/serve.py [--port 8502 --backend http://127.0.0.1:8000]
  or docker compose up --build -d --no-deps --wait frontend2. Python3.10+ stdlib;
  Compose backend default http://backend:8000; gateway env in frontend2/README.md.
- UI tests: python -m unittest discover -s frontend2/tests -p test_server.py -v;
  npm --prefix frontend2 ci --ignore-scripts; npm --prefix frontend2 test.
- Ruff backend config backend/pyproject.toml; frontend scoped E4,E7,E9,F with test
  E402 excluded. git diff --check. One-off logs/evidence under codex_helper/.

## Dependencies and environment

Pinned Python3.12/uv, Docling/OCR/CPU Torch, PG17/Qdrant, Streamlit/httpx,
LangGraph/Transcribe; versions in locks/C5. EU Bedrock GPTOSS/Qwen roles, Nova/Titan
1024D, eu-central-1 default. Word uses LibreOffice Writer/fonts,per-job profiles,
180s conversions. Polly neural16kHz mono16bitWAV, PCM-fitted visemes,
SSML2800billed/5800total. EN/IT/FR/DE math smoke verified. PS5.1 JSON: -Encoding UTF8;
curl.exe --% JSON single line.

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

Sequential workers/A3v2 variants;already removed audio needs fresh synthesis, not
automatic restoration. Archived packages have no TTL; unsaved expired text is read-only. Word complex
layout/equation/order fidelity requires review. Non-UTF8/headerless CSV,CSV URLs,
A7 idempotency/structural edits and mid-session level switching remain future.
Gateway/Moodle callbacks/model access,HA/backups/erasure/orphans unverified.
Independent post-edit review/grounding deferred. Live generation needs user
permission(granted for Word test);tests use fake providers. Semantic findings and
historical A7 failure diagnostics live in codex_helper reports. No auto-resubmit.
frontend2 review rendering verified on desktop/mobile; audible playback unverified.
Escaped text has no rich Markdown/LaTeX typesetter. jsdom is test-only; no runtime npm.

## Recent decisions

- Teacher-selected difficulty levels added to frontend2 and A3v2; defaults preserve
  all-level requests. No live AI generation was submitted to validate this update.
- Teacher review lists durable text independently of audio. User approved root
  course_archive/ retention so saved packages stay playable/revisable beyond24h.

## User notes (preserve verbatim)

## Last verified

2026-10-16 archive: config/Compose/storage/publication/delivery/revision/deletion/
session/catalog/review paths and frontend2 expiry UI checked. 106 isolated backend
lifecycle tests plus 11 final archive tests pass; 29 DOM/15 bridge tests pass.
Ruff, JS syntax/diff, Compose config and backend/frontend2 builds pass. API, all
workers and frontend2 refreshed healthy. Startup archived one intact existing
course (34 JSON/WAV files); live A5/disk checksums match, expiry omitted. Six
text courses remain listed; five missing old packages were not regenerated.
Edge live review QA passes at1400x1000/390x844. Evidence codex_helper/archive-* and
review-*. No live AI/TTS calls. Documentation and ignored archive directory updated.

Prior isolated validation: 2026-10-15 selected-level A3v2/A7/B7 112 tests;
2026-10-14 full backend955/Streamlit115 tests. Historical Word/CSV/semantic reports
under codex_helper are DRAFT evidence, not approval or current availability.
