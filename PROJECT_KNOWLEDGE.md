# Project Knowledge

## Purpose and current state

UPRAISE WP7 implements A1-A8/A3v2,B1/B2/B4/B5/B7,C1/C2/C3/C5;B3/B6/C4 reserved.
C3 source/images ready; live services still need additive migration/recreation.
Streamlit is an HTTP testbench/player. PG DRAFT text/plans and Qdrant sources persist;
A5 JSON/WAV has independent24hTTL. Moodle owns human approval. Do not stage/commit
without request. ../Uprise/v2 is reference-only. Semantic source/math/review
defects and historical course deletion evidence are documented under codex_helper.
frontend2/ is a separate client-facing UPRAISE visual prototype, pending UI approval
before backend integration. Static HTML/CSS/JS, curated sample data, memory only.

## Architecture and data flow

- Streamlit4-stage HTTP workspace: A1 IDs, A2 sources, one-source/v2/autofilled Draft,
  paginated A8 Jobs grouped job->level->chunk, deferred WAV/hash download; Advanced
  UUID/A4/A6. Retry keys survive navigation, auth/context clears caches, no auto
  mutations. Standalone v1/v2. EN/IT25neural voices, Joanna/Bianca defaults.
- frontend2 has Upload material / Generate course / Start learning. File selection
  retains metadata only; generation/playback/questions are explicitly simulated.
  No backend/network calls, audio, storage, or auth; CSP connect-src 'none'. English
  sample lessons vary by level; language/voice settings are illustrative. Reset or
  reload restores fixtures. Keep disconnected until user approves the interface.
- A1 PDF Docling/vision/math, CSV CsvProcessor or DOCX/DOC WordProcessor -> Titan
  -> ordered Qdrant chunks;active attempt fences revisions.document-ingestion-v1.6;
  CSV selects .csv/MIME,
  UTF-8/BOM, unique nonempty headers, comma/semicolon/tab; whole records, repeated
  headers, strings/empty values preserved. Limits100MB/100krows/256columns, oversized
  header/record fails422; no truncation/OCR/AI extraction. A1 IDs/A4/A6 unchanged;
  old PDF metadata/fingerprints preserved. Signed Moodle URL remains PDF-only.
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
- CSV page_count0/page_numbers[]/page=null; row_count/column_count, source_blocks/
  count/row_ranges (header1, multiline cells one record). A3 mixed-source row-group
  windows adapt legacy numeric slots to CSV blocks. Public pages[]/row ranges/
  [CSV rows N-M] text; speech omits markers. A7 all-level/B4 preserve rows. PDF
  source/review JSON keeps original shape for old PG; CSV remains UNVERIFIED.
- Word Docling + bounded LibreOffice DOC conversion/rendering;>5%token mismatch
  or fragmented text selects PDF fidelity. Recovered clipped paragraphs retain
  review flags/XML refs;pages[]/page_count0,blocks/word refs/extraction/rendered pages.
  [Word blocks N-M] omitted from speech;A3/A7/B4/UI share lifecycle. Native picture
  verification binds picture refs without fabricated PDF pages.
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
  whole lesson,sources preserved. Durable tombstones/fences/recoverable503;repeat204.
  Partial atomic PG/manifest/hash update preserves sibling IDs/plan/expiry;whole
  deletion scrubs/cancels. Running selected-level409;expired text deletable,no audio
  revival. A2 cleans B7 artifacts. UI requires pinned scope/confirmation/manualretry.
  New frontend modules require Docker COPY and packaging allowlist.
- C1 health/ready; C2 cached dependency evidence plus fresh shared control reads,
  UNKNOWN without recent evidence,no AWS polling. C3 admin:operations PUT uses
  global/course_id scope;enabled=true activates a disable. Runtime PG controls
  combine with deployment disables (cannot override);answers/generation/adaptive_levels/
  stt/tts/all. State+audit outbox commit atomically; idempotent admin_audit projection
  retries via operations refresh. Shared30writes/min/client,1000runtimecourse limit.
  API/worker admission fails closed on unavailable control store;queued jobs pause,
  claim races restore attempts. B2 rechecks after audit,PROCEED/discards score when
  adaptation/all disabled;start level pinned,no set-end. In-flight AI not hard-cancelled.
  Health UI confirmed manual PUT;local admin requires explicit development opt-in.
  C5 source/config/model/prompt hashes; A7 revision refs/strategy,
  max2alignment/max2final corrections, chunk-revision-v3. Checkpoints bind config.
  Human approval remains external; all results DRAFT.

## Important paths

- backend/src/upraise_ai/{api,schemas,workflows}/ contracts; generated_content.py/
  lesson_deletion.py B7; backend/docs/ contracts. services/lesson_*.py/v3 math/
  speech, aligned_*.py A3v2, revision_*.py A7, lesson_replacement.py publication/
  cleanup/consolidation. storage/{lessons,sessions,courses}.py persistence;
  workflows/{sessions,interrupts}.py/guards/session_jwt.py runtime;
  storage/controls.py + workflows/controls.py + docs/c3.md C3 control/audit contract;
  services/pdf/ + csv_processor.py/word_processor.py/source_references.py A1 and
  block-to-teacher adapters;backend/docs/{csv,word}-ingestion.md;versions.py C5.
- frontend/{ingestion_workspace,chunk_revision,revision_progress,generated_deletion,learning,
  learning_session,learning_interrupt,lesson_audio}.py; tests/: AppTest/fakeHTTP.
  frontend/README.md UI commands; backend/{pyproject.toml,uv.lock,Dockerfile,
  math_runtime/} builds. Chunk uniqueness(job_id,level_key,index), legacy level_key=''.
- frontend2/{index.html,styles.css,app.js,demo-data.js,favicon.svg}; README.md explains
  preview boundaries. serve.py exposes only static assets on local loopback.
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
  isolated suite; empty -k breaks PS. Validation uses labeled disposable PG/Qdrant,
  fake providers,no live volumes/credentials;default image upraise-word-validation
  includes LibreOffice. Rebuild via codex_helper/word-ingestion/Validation.Dockerfile.
- Frontend: docker compose run --rm --no-deps -v ./frontend:/workspace:ro frontend python -m unittest discover -s /workspace/tests -q
- Visual prototype: python frontend2/serve.py -> http://127.0.0.1:8502 (optional
  --port). Python3.10+, modern browser; no install/Docker needed. Syntax: node
  --check frontend2/app.js and node --check frontend2/demo-data.js.
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

## Recent decisions

- A7 replaces existing chunks; optional all-level atomic edit, processing IDs hidden.
- A2/B7 retained-content deletion; A6 temporary cleanup. Changed hash closes sessions.
- B7 user-approved extension; B6 reserved. CSV uses A1/Python csv; PDF keeps Docling.
- Word DOCX/DOC uses native Docling/retained rendering and existing visual fallback;
  source quality flags remain independent of DONE. No new final semantic judge.
- User requested a disconnected visual review before connecting frontend2. Use a
  buildless static prototype for this stage; existing frontend/backend are separate.

## User notes (preserve verbatim)

## Last verified

2026-10-14 frontend2: static source/README/serve.py; JS/Python syntax, HTML/CSS
parsing, local HTTP assets/non-asset denial, 18 DOM interaction checks passed.
Ad hoc jsdom harness/dependencies are ignored under codex_helper/frontend2-validation/.
Visual browser QA unverified: no browser available through the preview tool.

2026-10-14 C3:api/operations,guards/{ingress,operations},storage/{controls,audit,
jobs,lessons,sessions},workflows/{controls,sessions},workers/{ingestion,lessons},
services/{operations,versions},config/schemas,frontend operations/tests,docs/c3.md.
107focused/955fullbackend and115UI pass;Ruff,diff,Compose config,backend/frontend
builds pass;new image OpenAPI exposes C3. Disposable stores/fake providers only.
Live migration/recreation pending;local admin defaults off.

Prior Word/CSV quality defects remain in codex_helper/word-ingestion/*quality*.md
and csv-course-751551-*;formula/level/reviewJSON/citation/SSML defects;audio unheard.
Historical DRAFT evidence does not establish current availability/human approval.
