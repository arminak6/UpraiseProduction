# Project Knowledge

## Purpose and current state

UPRAISE WP7: A1-A8/A3v2,B1/B2/B4/B5/B7,C1/C2/C3/C5;B3/B6/C4 reserved.
PG DRAFT text/plans and Qdrant sources persist; A5 staging TTL24h, completed packages
saved in course_archive/. Moodle owns approval. No stage/commit without request.
../Uprise/v2 reference-only; semantic findings/evidence in codex_helper/.
frontend2/ is the proof-of-concept UI on localhost:8502 via stdlib HTTP bridge.
It presents draft generation, review and playback. C3 schema exists.

## Architecture and data flow

- frontend2 bridge: bounded routes/headers,TLS/Nonce/Host/Origin,OAuth/mTLS;
  JWT/retries tab RAM, no browser storage/direct AWS/autoPOST/A6. WAV/hash/session/
  offset/B5 fences; uncertain A7 needs new request. Levels1+,default all. Visible
  B4 Q&A lasts one interruption; Continue clears UI/audio. Backend RAM ends at
  B5/expiry/revocation/restart; last4pairs,2000chars/message, stable request UUID.
  voice.js/worklet: <=60s, preview/discard/send mono16kHz16bit WAV, pinned retry.
  Voice cap6000000+65536bytes,JSON128KiB. Nav/hide/end cancels mic/releases blobs.
  Localhost/HTTPS,mic permission,STT IAM needed; no returned transcript. See frontend2/README.md.
- Teaching versions differ from frontend2/A3v2; V1 default. V2.8 (`v2`) retains
  first authoring/translation/speech after failed review; A7 keeps teacher edits.
- V2.9: writer/reviewer/one judge/fresh review; flagged C, unusable C falls back A.
- V2.9 Light (`v2.9-light`) shares V2.9 agents/prompts; course-scoped Polly
  Standard audio/visemes, A7 and B4. Regular versions keep configured engine.
  core/teaching.py shares the schema selection and isolated speech settings;
  teaching_v29/light.py and frontend2 retain distinct course/review provenance.
- V3: document-order/equation plans, whole paragraphs,24k continuation, all-level
  reviews before TTS; entailment/coverage/decimal/case checks, bounded recovery.
- V3.1: heading/text recovery, chunk IDs, host citations, verified record negatives;
  strict A7, speech/tasks preserve subjects. Latest audit still shows regressions.
  Implementations teaching_v{2,29,3,31}/; backend/docs/teaching-v{2,29,3,31}.md.
- Learning badges read A8 teaching_version and stored per-level teaching_policy.
  Historical omitted request version means V1; V2.8 needs consistent policy,
  otherwise V2. Missing/unsupported provenance shows unknown. No backfill needed.
- Nav: Upload/Generate/Teacher review/Start learning. Review filters course/level/
  flags; PG text, notes, sources, available audio (V1 included). In-tab feedback/A7
  selection; expired text readable, A7 needs temporary/archived output. Ownership/
  deletion/publication fences; candidates hidden, content-free audit.
- Archive: ignored course_archive/, UPRAISE_LESSON_ARCHIVE_DIR on API/workers.
  Manifest/WAV/hash verified; A5/A7/A8/B1 survive TTL. A7/B7 supersedes, B7/A2
  erases owner archives; A6 withdraws delivery. Startup rescues intact packages;
  artifact_job_id/hash/manifest fences ownership. No chat archive/audio regeneration.
- A1 Docling/vision/math PDF, CsvProcessor, WordProcessor/LibreOffice fidelity
  fallback and PowerPointProcessor -> Titan/Qdrant chunks; attempt fences, ingestion-v1.8.
  CSV UTF8/BOM headers, comma/semicolon/tab, complete rows; Word block refs, no
  invented PDF pages. PDF quality fingerprints/contracts remain.
  PPTX v2: native XML geometry (columns/groups/RTL/merged tables), Docling evidence
  + Impress. Full-slide checks retain native words/exact numbers; visible picture
  evidence bounded/full resolution; masked/covered/transformed pictures render-only.
  Separate slide mapping/prompts/cache; bad/truncated candidates rejected. A2/A4
  readiness/blocking slides; both A3 APIs/workers block incomplete/legacy PPTX by
  default. Strict allow_incomplete_source=true permits available-content drafts;
  stored per job/retry identity. UIs require a per-course choice, reset on source/
  new course. Rejected candidates stay excluded; source warnings persist through
  generation/A7 at every level. Default false preserves legacy fingerprints.
  Existing courses readable. Native audit, slide-local chunks, original ordinals/
  slide refs, empty PDF pages. Notes/hidden slides opt-in. Details: powerpoint-ingestion.md.
  A3/A7/B4 retain slide refs; speech omits markers. Teacher review explicitly
  loads source PNGs via version/owner/hash-fenced document slides GET. A6 purges
  previews, retains source.pptx; A2 revokes access. Legacy PPT/media excluded.
- A2 deletes dependent PG/plans/artifacts/feedback; writer/admission fences and
  durable503 recovery. IDs/audit remain; fresh A1 allowed.
- A3v1 ordered5page windows/V3 roles/checked translation/speech. A3v2 shared plan/
  units across levels, distinct chunk/audio IDs. Text precedes TTS; target200/
  max500words,2refinements;v2.5/promptv6 ID recovery, rejects ambiguity/truncation.
- A3v2 levels:1-8 configured names, omitted/null=all, config order. Plan level if
  selected, else first selected. Fingerprint ignores order, conflicts on selection.
- CSV/Word pages[]/page_count0; source_blocks/CSV row_ranges/Word refs. CSV header
  row1, multiline cells one record. A3/A7/B4 preserve refs, omit row/block labels
  from speech; PDF unchanged. Extraction does not establish accuracy.
- A4 level/total counts; A5 bundle/learner_level slice shares full integrity hash;
  A8 expands before pagination. A6 fences writers; retained text/plans independent.
- A7 source-pinned unit edits, optional levels,2corrections; TTS-safe cache, atomic
  PG/manifest/hash/WAV, stale409, unrelated edits merge, current-result.json recovery.
  ChildA6 protects current;rootA6/A2 removes. Manual cross-level; failed candidates
  unaudited/erased by A6/TTL/A2/replacement. docs/a7.md.
- B1 binds job/context/hash/level. Local DRAFT preview or signed OAuth/mTLS
  approval/consent; RAM30min capped by token/lesson, clears on restart/hash change.
  B2 supplied score>.5 HIGH_COGNITIVE_LOAD else PROCEED;consent,no measurement.
  B4 source-scoped PG/Qdrant LangGraph:text/WAV-STT->answer/SSML/WAV/visemes;
  TTS outage retains text,grounding=null/GROUNDING_NOT_VERIFIED. B5 late fences,
  RAM clear/idempotent content-free audit30min. Contracts:docs/b1-b5.
- B7 DELETE /v1/lesson-jobs/{id}?learner_level checks client/course access and preserves sources.
  Partial/whole deletion, tombstones/503 recovery/manual retry: docs/b7.md.
- C1 health/ready; C2 cached evidence/no AWS polling. C3 admin:operations controls,
  enabled=true disables; fail-closed queued pause, audit outbox. docs/c3.md.
  C5 hashes/provenance/checkpoints. Human approval external; all results DRAFT.

## Important paths

- backend/src/upraise_ai/: api/schemas/workflows contracts, services/lesson_*,
  aligned_* and revision_* lifecycle; pdf/,csv_processor.py,word_processor.py,
  powerpoint_{processor,layout,vision}.py,source_readiness.py ingestion/admission;
  storage/ persistence; guards/session_jwt.py session assertions.
- backend/docs/api-reference.md catalogs all 25 backend HTTP operations and their schemas.
  Other backend/docs/ files cover teaching versions, ingestion, lifecycle and operations.
- frontend2/tests/auth_demo/ runs Frontend2 with sign-in on 8502 via workspace.compose.yaml.
  The UI bridge checks the browser session, requests a client-credentials JWT, and
  calls the production-mode backend through mTLS. Separate PG/Qdrant for demo-course;
  processing workers are absent. Login and Frontend2 share the magenta logo and styles.
  Standalone browser (8513) and curl (8514) demos use a sample document. See its README.
  Normal Compose serves one-hour test JWTs at POST /oauth/token alongside /v1 APIs on port 8000 in development JWT mode.
  Regular Frontend2 posts client credentials and grant_type to that endpoint before each
  backend request. It uses the main stack's data and workers. Its upload form selects
  demo-course, the course authorized by local JWTs. The UI keeps credentials server-side.
  Production calls use the configured OAuth issuer and mTLS. See backend/docs/local-testing.md.
- frontend2/{app,api,ui,voice,voice-capture-worklet}.js,serve.py,index.html,styles.css,
  tests/{test_server.py,ui.test.cjs}. README explains auth/run and bridge allowlist.
- Archive: storage/lesson_archive.py, services/lesson_replacement.py,
  workflows/archive_lessons.py. Matching integration tests cover erasure/TTL/A7.
- .gitignore excludes dependency and tool caches, codex_helper/ evidence,
  course_archive/ data and FUTURE_UPDATES.md. Assessment evidence is indexed below.
  Fewer flags alone do not establish teaching quality. No listening assessment performed.
- codex_helper/aws-cost-report/: measured API costs and PDF generators. Regular
  2.9/Light comparison: generate_light_comparison_report.py; latest report is
  output/pdf/upraise-55-page-aws-cost-report-2026-11-02-with-light-qa.pdf, with
  Table 8 for Light Q&A planning estimates. Original *with-light.pdf retained.
- codex_helper/voice-quality-v29-light/: technical WAV audit, approved sampled
  AWS transcripts, source/narration findings, and listen.html with matched clips.

## Commands

- Docker Desktop Linux; API8000/frontend2 8502. Build:
  docker compose build backend frontend2. Migrate:
  docker compose run --rm --no-deps migrate. Refresh changed shared backend services:
  docker compose up -d --no-deps --wait --wait-timeout 180 backend worker lesson-worker lesson-delivery
  Default Compose starts Frontend2 and uses /ready for backend health. Its seven
  long-running services have unless-stopped restart policies; migrate is a one-shot.
- Drain jobs before pipeline/config changes; restart clears RAM sessions. Never
  reset live volumes/re-ingest sources for A3. Runtime pipeline default lesson-v3-a3-12.
- codex_helper/validate-a3v2.ps1 -Selection 'not codex_nonexistent_sentinel': full
  suite; empty -k breaks PS. Disposable PG/Qdrant/fake providers, no live data;
  image upraise-word-validation (codex_helper/word-ingestion/Validation.Dockerfile).
- UI run/auth: frontend2/README.md; local python frontend2/serve.py (3.10+stdlib).
  Docker static assets are copied, not mounted; rebuild/recreate frontend2 after
  UI changes. New runtime assets must be allowed by frontend2/.dockerignore.
- UI tests: python -m unittest discover -s frontend2/tests -p test_server.py -v;
  npm --prefix frontend2 ci --ignore-scripts; npm --prefix frontend2 test.
- Fresh Ubuntu Git clone: README Start covers credentials, archive permissions,
  Docker startup at boot and Compose readiness. Git carries source and templates.
  Transfer existing course_archive and Docker data separately when needed.
- Ruff backend config backend/pyproject.toml; Frontend2 scoped E4,E7,E9,F with test
  E402 excluded. git diff --check. One-off logs/evidence under codex_helper/.

## Dependencies and environment

Python3.12/uv,Docling/OCR/CPU Torch,PG17/Qdrant,httpx,LangGraph/Transcribe;
EU Bedrock GPTOSS/Qwen,Nova/Titan1024D,eu-central-1. Word/Impress LibreOffice/fonts180s;
python-pptx1.0.2 locked. PPTX100MiB/1000slides/1000MiB expanded/10000ZIPentries.
Polly default neural (V2.9 Light standard),16kHz mono16bit,PCM visemes,
SSML2800billed/5800total. Local Python3.10
cannot run backend: use Docker. PS5.1 JSON explicitly UTF8; pipe single-quoted here-strings.

## Conventions and invariants

UPRAISE_LESSON_LEVEL_PROFILES ordered JSON1-8profiles; plan level must match,
batch4/max8. Explicit level array survives PG JSONB ordering. Chunk identity
(job_id,chunk_id), index0-based; content_unit_id links levels. Source content_hash
opaque/source_hash realSHA256. Defaults: PDF100MiB/1000pages; Word100MiB/
1000 rendered pages and DOCX1000MiB expanded; CSV100MiB/100krows/256columns.
Sources untrusted; parsing does not establish accuracy.
Logs/audit omit text/scores/audio/prompts/private provider errors; optional local
diagnostics removed by A6/TTL or successful replacement. Ownership/source/leases/
artifact locks fence publishing; corrupt503/pending409/expired410. Attempts3/max12h,
supervised killable children; progress does not reset deadline. Regular APIs check
authenticated client and course. C3 requires admin:operations.
Missing production auth config closes ingress; no Redis/session renewal.

## Known limitations and next work

Workers/variants sequential; missing audio needs synthesis, archive has no TTL.
Unsaved expired text read-only. Word fidelity,gateway/Moodle/HA,backups/erasure/
orphans need review;V1 grounding deferred. NonUTF8/headerless CSV/URLs, structural
A7/idempotency, mid-session levels, persistent flag dismissal/listening QA/LaTeX
renderer remain future work.
Preserve old courses; no unsolicited generation. V3.1 same-source course scores5/10; see Last verified.
Fresh PPTX upload runs updated checks; incomplete extraction blocks generation
by default, with explicit available-content opt-in. Media/OLE, animation sequences and renderer fidelity remain bounded
limitations; full-slide model checks are not human approval. See powerpoint-ingestion.md.
Latest live FondoEst re-upload remains INCOMPLETE on slide12: native spelling
variant triggers rejection; candidate also duplicates/misaligns the price table.

## Recent decisions

## User notes (preserve verbatim)

## Last verified

2026-11-25: Built default Compose with fresh disposable volumes. Verified seven
restart policies, migration exit 0, /ready and Frontend2 JWT HTTP 200. Forced
backend process exit 42, verified Docker restart count rose and JWT calls recovered.

2026-11-25: Removed the standalone Streamlit source and Compose service. Verified its
port closed and the backend and Frontend2 on ports 8000 and 8502.

2026-11-25: Regular Frontend2 on 8502 uses the main backend's JWT endpoint.
Confirmed direct unauthenticated documents request returns 401 and the UI bridge
returns the existing document and lesson with 200. 19 bridge and 81 UI tests passed.

2026-11-25: Matched Frontend2 branding to the transfer template. Verified Docker build,
18 bridge tests, 80 UI tests, and isolated login, JWT issuance/rejection, API permissions
and sign-out through the auth demos.

2026-11-25: Verified one-hour JWT issuance without a scope claim on port 8000, regular API
access, foreign-course denial and the admin check. Both Docker auth demos passed, including
a sample upload. Local settings are in ignored .env.

2026-11-24: Local auth demo exercised login, OAuth issuance, HTTPS signing-key
lookup, mTLS, JWT validation and API permissions in a disposable Compose container.
Port 8502 workspace authenticated real read/upload routes in isolated storage;
Chrome verified sign-in, all four sections and sign-out. 80 UI and 18 bridge tests passed.
Service JWT `iat`/`sub` are optional with an allowlisted identity. Ingress unit and
auth demo checks passed with the shorter token.

2026-11-18: Synced frontend2 proof-of-concept copy and sidebar changes. Checked
frontend2/index.html, app.js and UI assertions against the transfer checkout.
Verified ingestion defaults in compose.yaml, .env.example and core/config.py.

2026-11-13: compose.yaml, Dockerfiles/.dockerignore for backend/frontend/frontend2,
root README/.env.example/.gitignore, local-testing.md and AWS credential loader
checked for Ubuntu source transfer. Named volumes, credentials and live data are outside
the ZIP. Compose needs course_archive, aws_credentials.json and callback_tls bind sources.

2026-11-10: Light report generator, saved rate data, AWS Polly pricing and
report-data-2026-11-02-with-light-qa.json checked. Two-page *with-light-qa.pdf
rendered and visually verified; first page identical, Tables 4-7 text retained.
Table 8 reprices only audio/marks at $4 vs $16 per million characters, assuming
6 characters/word; bounds rounded outward to $0.001. Planning only, not measured.
Original PDF overwrite was blocked by Windows; updated copy saved alongside it.

2026-11-02: V2.9 Light added; core/teaching.py, teaching_v29/light.py, generation/
review/catalog schemas, worker, B4, frontend2 selector/labels and teaching-v29.md
checked.95 backend and80 UI tests passed; Ruff/diff clean. Fake providers and
disposable PG/Qdrant only; no agent-initiated generation/listening test. Images
rebuilt; stopped containers refreshed. Later user-run Light costs saved in
aws-cost-report/measured-v29-light-*.json: three levels total $1.70 vs regular $4.89.
Two-page comparison PDF/data generated from these files and measured-cost-data.json;
rendered pages and totals verified, with ingestion/Q&A retained separately.
Voice audit:199 WAVs passed technical checks;18 passages/24.4min transcribed with
explicit approval (~$0.25). Recognition mismatches are diagnostic, not listening
scores; source/narration concerns affect both versions. No perceptual listening.
Provisional source-fidelity judgments (~7/10 each) are in source-fidelity-ratings.md;
sample-based editorial ratings, distinct from ASR agreement and naturalness.
Local ASR hit memory limits; Docker API returned500, so standalone AWS helper used.

2026-10-28: Available-content generation implemented/deployed. Both A3 APIs and
workers honor strict allow_incomplete_source; warnings/models/revisions preserve
incomplete coverage.64 focused checks,29 final integration/quality checks,77 UI,
18 bridge,38 testbench passed (overlap). Services healthy, assets/schema verified;
original source/course/21parts unchanged by hash. Latest source60897fc9 remains
INCOMPLETE slide12/14chunks, now usable by explicit choice; no live generation
started by this work. codex_helper/available-content-generation/validation.md.

Earlier live FondoEst comparison: identical bytes,1442 editable tokens retained;
15/14 indexed chunks match. Latest Italian doc60897fc9/job ea5f14ac:12/13 checked,
113/124 checked elements;11 flagged on12. Exclusions8/order9 fixed, prices12 absent:
famigliari->familiari plus duplicated/misaligned candidate table. Previews unchanged.
codex_helper/fondoest-ingestion-comparison/comparison.md. Copied validation below
is distinct from this live result; assessment made no app/data/model changes.

Earlier PPTX v2/v1.8 rollout:1367 backend+93 focused,75 UI,18 bridge,37 testbench.
Copied-source real-model validation passed13/13,114 checked,0 missing native tokens,
prices recovered; dummy vectors/no live generation. codex_helper/pptx-general/;
original V1 audit: codex_helper/fondoest-pptx-assessment/assessment.md.

Earlier voice UI/Edge synthetic mic and asset/nonce QA: codex_helper/voice-questions/.
2026-10-26 same-Word audit: V1/V2.9/V3/V3.1 scores5/6.5/5.5/5;V2.9 best draft.
V3.1 job2a45081b DONE/DRAFT,48m18s; regressions/omissions/duplication remain.
129 WAV hashes/durations,4 manifests, no listening QA. codex_helper/ paths:
gruppo-imprese-{four-version-comparison,assessment}/, v31-implementation/,
teaching-v{29,3}-comparison/.
