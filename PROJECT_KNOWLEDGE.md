# Project Knowledge

## Purpose and current state

UPRAISE WP7 implements A1-A8/A3v2,B1/B2/B4/B5/B7,C1/C2/C3/C5;B3/B6/C4 reserved.
C3 schema tables exist; API/workers refreshed with current source on 2026-10-15.
Streamlit is an HTTP testbench/player. PG DRAFT text/plans and Qdrant sources persist;
A5 JSON/WAV has independent24hTTL. Moodle owns human approval. Do not stage/commit
without request. ../Uprise/v2 is reference-only. Semantic source/math/review
defects and historical course deletion evidence are documented under codex_helper.
frontend2/ is the approved client UI, connected through a stdlib Python HTTP bridge.
Its Docker service runs on localhost:8502; original frontend/backend remain separate.

## Architecture and data flow

- Streamlit HTTP testbench: A1/A2, v1/v2 drafts, paginated A8/job-level-chunk,
  verified deferred audio, B1/B4/B5 and advanced A4/A6. Retry keys survive navigation;
  auth/context clears caches. EN/IT25neural voices; Joanna/Bianca defaults.
- frontend2: A1/A2 material, A3v2/A4/A5/A7 courses, A8/B1/B4/B5 learning. Same-origin
  route/header-limited bridge; nonce/Host/Origin checks, verified TLS/no redirects,
  server-side OAuth/mTLS env. Signed learner JWT stays in tab RAM. Real WAV SHA256,
  result-hash/session/expiry checks; B4 exact offset/retry, B5 cleanup/late-response
  fences. No seeded data/storage/AWS/direct DB; no automatic POST or A6 RECEIVED.
  A7 uncertain submission requires explicit new-request preparation. Backend calls
  remain authoritative; custom levels come from A5. Refresh loses local retry state.
  Generation checkboxes select 1+ configured levels; defaults all, retained in tab.
- A1 PDF Docling/vision/math, CSV CsvProcessor or DOCX/DOC WordProcessor -> Titan
  -> ordered Qdrant chunks;active attempt fences revisions.document-ingestion-v1.6;
  CSV: .csv/MIME, UTF8/BOM headers, comma/semicolon/tab, whole records/no truncation,
  OCR or AI extraction; oversized records422. Limits100MB/100krows/256columns.
  PDF metadata/fingerprints/A1-A6 contracts preserved; signed URL remains PDF-only.
  A2 owner-scoped source/course deletion across all levels/languages/statuses removes
  dependent PG/plans/artifacts/feedback, preserves minimal IDs/audit. Admission/
  source/writer fences, durable503 recovery, RAM revocation; fresh A1 after deletion.
  Original ingestion PDF/CSV/evidence retain independent artifact policy; A2
  removes indexed source material and dependent lessons, not immediate file purge.
- A3v1 ordered5page windows -> V3 roles -> checked translation -> PG text/SSML
  before Polly. A3v2 shared canonical plan, roles per level, distinct chunk/audio
  IDs, shared ordered units/source scope. Approved drafts kept, rejected drafts
  edited; independent post-edit A3 review deferred. All text/plan before speech;
  target200/max500words, max2shared refinements, no level-only splits.
  v2.5/promptv6 bounded ID-aware recovery, ambiguous/truncated output fails closed;
  original math/speech recovery retained.
- A3v2 optional top-level levels: 1-8 unique configured names; omitted/null=all.
  Snapshot only selected profiles in configuration order; plan at configured level
  if selected, otherwise first selected. Selection is in idempotency fingerprint;
  reordered same selection reuses job, changed selection conflicts for same key.
  No schema migration or pipeline signature change; existing jobs/v1 unchanged.
- CSV page_count0/pages[]/page=null; row/column counts, source_blocks/row_ranges
  (header1, multiline cells one record). A3 row-group windows/A7/B4 preserve rows;
  [CSV rows N-M] omitted from speech. PDF JSON shape preserved; CSV UNVERIFIED.
- Word Docling/LibreOffice; >5%token mismatch/fragmentation selects PDF fidelity.
  Clipped-paragraph recovery retains review/XML refs; pages[]/page_count0 and
  source_blocks/word refs. Native picture refs never fabricate PDF pages.
  [Word blocks N-M] omitted from speech; A3/A7/B4 share lifecycle.
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

Sequential workers/A3v2 variants;no archive read API/audio restoration. Word complex
layout/equation/order fidelity requires review. Non-UTF8/headerless CSV,CSV URLs,
A7 idempotency/structural edits and mid-session level switching remain future.
Gateway/Moodle callbacks/model access,HA/backups/erasure/orphans unverified.
Independent post-edit review/grounding deferred. Live generation needs user
permission(granted for Word test);tests use fake providers. Semantic findings and
historical A7 failure diagnostics live in codex_helper reports. No auto-resubmit.
frontend2 browser rendering/audible playback unverified; text is escaped without
a rich Markdown/LaTeX typesetter. Runtime has no npm dependency; jsdom is test-only.

## Recent decisions

- User approved frontend2 design and backend integration. Replaced demo fixtures
  with real APIs through a same-origin bridge.
- Teacher-selected difficulty levels added to frontend2 and A3v2; defaults preserve
  all-level requests. No live AI generation was submitted to validate this update.

## User notes (preserve verbatim)

## Last verified

2026-10-15 A3v2 schemas/workflow/API/docs and frontend2 JS/CSS/tests/README:
112 isolated backend tests (a3_v2/a7_all_levels/b7_integration), 21 DOM tests and
14 bridge tests pass; JS syntax/diff and backend/frontend2 image builds pass.
Backend validation uses disposable PG/Qdrant and fake model/speech providers.
No live generation/upload/revision/session mutations; browser QA unavailable.

Live 2026-10-15: updated API/worker/frontend2 containers healthy, OpenAPI levels
field and served checkboxes verified; bridge A2/A8 reads pass. Readiness DEGRADED
only for providers without recent evidence (embedding/Polly/Transcribe UNKNOWN).
Prior 2026-10-14 full suite: 955 backend/115 Streamlit tests and builds passed.
Historical Word/CSV quality reports remain under codex_helper; DRAFT evidence
does not establish current availability or human approval.
