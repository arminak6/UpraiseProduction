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
- Teaching versions differ from frontend2/A3v2; V1 default. V2.8 (`v2`) checkpoints
  first authoring/translation/speech; failed reviews flag originals, A7 retains
  teacher updates. docs/teaching-v2.md, teaching_v2/{first_draft,review_recovery}.py.
- V2.9: teaching_v29/, docs/teaching-v29.md. Writer -> reviewer -> one source-
  authoritative judge edit -> fresh review. A/C generation, B review model;
  failed C retained with flags, unusable C retains A. Adaptations drop stale
  canonical findings, retain source warnings; A7 avoids legacy edit loops.
- V3: teaching_v3/, docs/teaching-v3.md. Document-order plan, equation definitions,
  whole paragraphs, continuation24kchars. All levels/boundaries reviewed before
  TTS; narrator sees actual prior text. Entailment/coverage, decimals, typed cases,
  subject conflicts, bounded edits/flagged recovery; strict A7, TTL/A6/A2 intact.
- V3.1: teaching_v31/, docs/teaching-v31.md. Heading/text recovery, explicit chunk
  IDs, host citations, verified record entailment/coverage negatives, bounded
  recovery/editing. Strict A7; speech/tasks preserve subjects. TTL caches and stage
  counters;8502 selection/review/cards. Latest audit: regressions,48m18s runtime.
- Learning badges read A8 teaching_version and stored per-level teaching_policy.
  Historical omitted request version means V1; V2.8 needs consistent policy,
  otherwise V2. Missing/unsupported provenance shows unknown. No backfill needed.
- Nav: Upload/Generate/Teacher review/Start learning. Review filters course/level/
  flags; PG text, notes, sources, available audio (V1 included). In-tab feedback/A7
  selection; expired text readable, A7 needs temporary/archived output. Ownership/
  deletion/publication fences; candidates hidden, content-free audit.
- Archive: ignored course_archive/, optional UPRAISE_LESSON_ARCHIVE_DIR on API/
  workers. Manifest/WAV/hash verified; A5/A7/A8/B1 survive staging TTL. A7/B7 removes
  superseded packages; B7/A2 erases owner archives, A6 withdraws delivery only.
  Startup rescues intact expired packages; no audio regeneration/chat archive.
  artifact_job_id/hash/manifest selects current owner paths.
- A1 Docling/vision/math PDF, CsvProcessor, WordProcessor/LibreOffice fidelity
  fallback and PowerPointProcessor -> ordered Titan/Qdrant chunks; active-attempt fences, ingestion-v1.7.
  CSV UTF8/BOM headers, comma/semicolon/tab, complete rows; Word block refs, no
  invented PDF pages. PDF quality fingerprints/contracts remain.
  PPTX native Docling + Impress renders; notes/hidden slides opt-in at A1.
  Original slide ordinals/source_blocks, slide_numbers/slide_references, empty
  public PDF pages. Chunk boundaries stay within slides. Verified visual
  descriptions only; failed comparisons/sparse slides flagged. Known defect:
  PPTX sends incompatible page/native mappings to verify_source; real checks
  reject before model calls. Screenshots become placeholders; not fixed yet.
  A3/A7/B4 retain slide refs; speech omits markers. Teacher review explicitly
  loads source PNGs via version/owner/hash-fenced document slides GET. A6 purges
  previews, retains source.pptx; A2 revokes access. Legacy PPT/media excluded.
- A2 deletes dependent PG/plans/artifacts/feedback; writer/admission fences and
  durable503 recovery. IDs/audit remain; fresh A1 allowed.
- A3v1 ordered5page windows/V3 roles/checked translation/speech. A3v2 shares a
  canonical plan/content units across levels, distinct chunk/audio IDs. Text/plans
  precede TTS; target200/max500words, max2 shared refinements. v2.5/promptv6 bounds
  ID-aware recovery, rejects ambiguous/truncated output; approved drafts skip edits.
- A3v2 levels:1-8 configured names, omitted/null=all, config order. Plan level if
  selected, else first selected. Fingerprint ignores order, conflicts on selection.
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
  Partial/whole deletion, tombstones/503 recovery/manual retry: docs/b7.md.
- C1 health/ready; C2 cached evidence/no AWS polling. C3 admin:operations controls,
  enabled=true disables; fail-closed queued pause, audit outbox. docs/c3.md.
  C5 hashes/provenance/checkpoints. Human approval external; all results DRAFT.

## Important paths

- backend/src/upraise_ai/: api/schemas/workflows contracts, services/lesson_*,
  aligned_* and revision_* lifecycle; pdf/,csv_processor.py,word_processor.py,powerpoint_processor.py
  ingestion; storage/ persistence; guards/session_jwt.py session assertions.
- backend/docs/ contains lifecycle/contracts, teaching-v{2,29,3,31}.md,
  a7.md, powerpoint-ingestion.md, lesson-review.md, lesson-archive.md, b1-b5, b7.md and c3.md.
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
EU Bedrock GPTOSS/Qwen,Nova/Titan1024D,eu-central-1. Word/Impress LibreOffice/fonts180s;
python-pptx1.0.2 locked. PPTX100MB/500slides/500MB expanded/10000ZIPentries.
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
PPTX source verification and spatial heading/value order need correction before
complete ingestion; FondoEst assessment and reproduction are indexed below.

## Recent decisions

## User notes (preserve verbatim)

## Last verified

2026-10-28: Read-only FondoEst PPTX audit:13slides/15chunks,1442 editable tokens
retained; live Qdrant IDs/text and source/render hashes match. Real verifier rejects
PPTX mappings (incomplete_source_mapping); permissive fake-verifier tests missed it.
All134elements NEEDS_REVIEW. Slide8 exclusions/slide12 prices absent from index/
all3 course levels. Slide9 associations weaken, course conflates limits. Preview
overlap/clipping; native PowerPoint fidelity unproven. Source7d3f44af/course0c508b95,
teachingV1. No app/course changes/model calls. Report/evidence/reproduction:
codex_helper/fondoest-pptx-assessment/assessment.md.

Earlier PPTX deployment:1347 backend +13 focused,73 UI,18 bridge,11 testbench
checks passed; synthetic deck/chart/browser evidence codex_helper/powerpoint/.
Services healthy,8502 assets match; ingestionv1.7. These checks did not establish
real source-verifier compatibility; current unresolved defect supersedes them.

Earlier verification: voice UI (69 UI/17 bridge checks; real Edge synthetic mic)
and deployed asset/nonce packaging evidence: codex_helper/voice-questions/.
2026-10-26 same-Word four-version audit: V1/V2.9/V3/V3.1 scores5/6.5/5.5/5;
V2.9 remains best teaching draft. V3.1 job2a45081b DONE/DRAFT,48m18s; restored
citations but retained learner-text regressions, omissions and duplicated parts.
129 WAV hashes/durations and4 manifests checked; no listening assessment.
Evidence/settings/source observations: codex_helper/gruppo-imprese-four-version-comparison/;
original source codex_helper/gruppo-imprese-assessment/. Earlier rollout fixes
and QA indexed in codex_helper/v31-implementation/ and teaching-v{29,3}-comparison/.
