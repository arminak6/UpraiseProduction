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
  B4 UI accepts text or microphone recording: voice.js + voice-capture-worklet.js
  capture <=60s, preview/discard/send mono16kHz16bit WAV via same B4 multipart.
  Bridge bounds voice body to6000000+65536bytes; JSON128KiB remains. Retry pins
  WAV/UUID/chunk/offset. Active mic stops on cancel/nav/hide/B5/expiry; blob/tabRAM
  released on success/discard/end. Localhost/HTTPS + mic permission; AWS STT IAM
  still required. Voice turns have a label, not a returned STT transcript.
- Teaching versions differ from frontend2/A3v2; V1 is default. V2.8 (`v2`)
  checkpoints first authoring/translation/speech before review; failures flag
  original text without rewrites. Format/identity/storage fences remain; large
  units split without rewording. A7 retains teacher updates on review failure.
  Details: docs/teaching-v2.md and teaching_v2/{first_draft,review_recovery}.py.
- V2.9: teaching_v29/, docs/teaching-v29.md. Writer -> source/level reviewer ->
  one source-authoritative judge edit -> fresh review. A/C generation model, B
  review model; judge checkpoint precedes final review. Failed review retains C
  with human flags; unusable C retains A explicitly. Current adaptations drop
  stale canonical findings; source warnings persist. A7 avoids legacy edit loops.
- V3: teaching_v3/, docs/teaching-v3.md. Document-order plan, same-page equation
  definitions, adjacent continuations within24k teaching chars/full payload cap.
  Primary coverage differs from supporting citations; whole paragraph units.
  All levels/boundaries reviewed before TTS. Narrator gets actual prior writing
  and navigation, no future source; current-written entailment/reverse coverage,
  source-equal decimals, typed cases and question reading. Context/policy/model
  caches; explicit subject/assertion conflicts. Bounded edits then flagged DRAFT
  recovery. A7 strict; TTL/A6/A2 preserved. No extraction repair or guessed facts.
- V3.1: teaching_v31/, docs/teaching-v31.md. Restores selected original text and
  heading evidence, recomputes references. Writer/adaptation/A7 select actual
  chunk IDs; host renders citations. Cited-record entailment/coverage negatives
  independently verified; host-selected targets preserve passed paragraphs.
  Per-record source/canonical recovery fits word limits and flags editing; A7 strict. Final learning
  task review flags incomplete level adaptation; speech checks question roles and
  subjects. Parsed author checkpoints and full-evidence/policy/model verdict
  caches follow job TTL; per-stage timing/cache counters. Latest same-source audit finds learner-text regressions
  and a48m18s run; see Last verified. UI8502 selector/cards/review support v3.1.
- Learning badges read A8 teaching_version and stored per-level teaching_policy.
  Historical omitted request version means V1; V2.8 needs consistent policy,
  otherwise V2. Missing/unsupported provenance shows unknown. No backfill needed.
- Nav: Upload / Generate / Teacher review / Start learning. Review selects courses/
  levels, counts and filters flagged/all sections; original PG text, expanded notes,
  sources and available audio (including V1). Feedback/A7 selection persist in-tab.
  Expired text readable; A7 needs completed temporary/archived output. Ownership/
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

- backend/src/upraise_ai/: api/schemas/workflows contracts, services/lesson_*,
  aligned_* and revision_* lifecycle; pdf/,csv_processor.py,word_processor.py
  ingestion; storage/ persistence; guards/session_jwt.py session assertions.
- backend/docs/ contains lifecycle/contracts, teaching-v{2,29,3,31}.md,
  a7.md, lesson-review.md, lesson-archive.md, b1-b5, b7.md and c3.md.
- frontend/ Streamlit; frontend2/{app,api,ui,voice,voice-capture-worklet}.js,serve.py,index.html,styles.css,
  tests/{test_server.py,ui.test.cjs}. README explains auth/run and bridge allowlist.
- Archive: storage/lesson_archive.py, services/lesson_replacement.py,
  workflows/archive_lessons.py. Matching integration tests cover erasure/TTL/A7.
- codex_helper/ ignored helpers/evidence; FUTURE_UPDATES.md ignored backlog.
  Assessment/rollout evidence is indexed in Last verified; fewer flags alone
  never establishes higher teaching quality. No listening assessment performed.

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
  Docker static assets are copied, not mounted; rebuild/recreate frontend2 after
  UI changes. New runtime assets must be allowed by frontend2/.dockerignore.
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

Workers/variants remain sequential. Missing audio needs synthesis; archive has
no TTL. Unsaved expired text is read-only. Word fidelity, gateway/Moodle/HA,
backups/erasure/orphans need review. V1 final grounding deferred. NonUTF8 and
headerless CSV/URLs, structural A7/idempotency and mid-session level changes are
future work. No persistent human-dismissal status, listening QA or LaTeX renderer.
Preserve old courses; no unsolicited generation. V3.1 same-source course scores5/10; see Last verified.

## Recent decisions

## User notes (preserve verbatim)

## Last verified

2026-10-28: Voice-question UI/bridge/recorder/Docker assets and README checked.
69 UI +17 bridge tests passed; real Edge AudioWorklet with synthetic mic through
actual Python bridge verified WAV/preview/retry/offset/cleanup/mobile. No AWS
calls or live speech recognition; evidence codex_helper/voice-questions/.
Added missing voice.js/worklet Docker allowlist; rebuilt/recreated only frontend2.
Live8502 hashes match source; isolated browser confirms visible/enabled voice
button and loaded recorder. Existing browser tabs require refresh.

2026-10-26: Four-version same-Word audit, new V3.1 job2a45081b DONE/DRAFT,
1attempt/48m18s/10parts per level. Same source attempt/request/model IDs as prior
V1/V2.9/V3. Scores5/6.5/5.5/5; V2.9 remains best teaching draft. V3.1 restores
founders/citation resolution but exposes UUIDs/review prose, English passages,
omits block24 ethics and duplicates5/10 advanced/intermediate parts. All129 WAV
hashes/durations and4 manifests verified;30 new written/spoken scripts read,
no listening assessment. No application/course changes or model/TTS requests.
Evidence: codex_helper/gruppo-imprese-four-version-comparison/{assessment.md,
metrics.json,settings.json,content-observations.json}. Original source in
codex_helper/gruppo-imprese-assessment/; earlier audits indexed by new report.
Prior fixes/tests/rollout: codex_helper/v31-implementation/
{patch-scope-fix.md,material-recovery-fix.md,material-fix-verification.json}.
Earlier QA: codex_helper/{teaching-v29-comparison,teaching-v3-comparison}/.
