# UPRAISE client interface

The client-facing UPRAISE interface has four sections: **Upload material**,
**Generate course**, **Teacher review**, and **Start learning**. It connects to the existing backend
through a small Python HTTP bridge. The original Streamlit `frontend/` remains
available independently.

**Version 2.9** is an opt-in teaching/narration choice. It runs writer, reviewer,
one judge/editing pass and a fresh final review. Unresolved final findings keep
the judge's output with human-review tags and continue generation. Version 2.8
remains separately selectable; existing courses keep their recorded versions.
See [V2.9 behavior and API selection](../backend/docs/teaching-v29.md).

**Version 2.9 Light** uses that same teaching workflow with a lower-cost
Polly Standard voice for lessons, teacher updates and spoken questions/answers.
Select it separately from Version 2.9; Standard may sound less natural than Neural.

## Run

With the backend running, from the repository root:

```powershell
python frontend2/serve.py
```

Open **http://127.0.0.1:8502**. Stop with Ctrl+C. Python 3.10+ and a modern browser
are sufficient; there are no runtime pip/npm dependencies. Host-run defaults use
`http://127.0.0.1:8000` as the backend. Override either address explicitly:

```powershell
python frontend2/serve.py --port 8503 --backend http://127.0.0.1:8000
```

Or use the optional Docker Compose service:

```powershell
docker compose up --build -d --no-deps --wait frontend2
```

The backend must already be running for `--no-deps`. The service is also included
in the existing `tools` profile. Compose uses `http://backend:8000` by default,
binds the host port to loopback, and supports `FRONTEND2_PORT` (default 8502).
If an older prototype server is listening on that port, stop it first. Directly
opening `index.html` is no longer supported: the HTTP bridge is required.

## Connected behavior

- **Teaching version:** generation defaults to Version 1. Opt into Version 2 for
  concept-focused teaching, separate spoken formula explanations and additional
  checks of mathematical meaning. Each generation creates a new course; choosing
  Version 1 again leaves existing courses intact. V2 review shows the spoken script
  and lets teachers listen to verified audio for each part. Revisions inherit the
  course's version. V2 uses extra AI calls; human listening/approval is still needed.
  See [V2 behavior and rollback](../backend/docs/teaching-v2.md).
- **Version 3.1:** the version selector also offers source-aware teaching and
  level review. It preserves source subjects, uses evidence-linked citations,
  repairs affected paragraphs and flags incomplete difficulty adaptation.
  Course cards and teacher review retain V3.1 provenance and spoken scripts.
  See [V3.1 behavior and validation](../backend/docs/teaching-v31.md).
- **Material:** PDF/Word/PowerPoint (`.pptx`)/UTF-8 CSV upload through A1; browser SHA-256; real A2
  document library and progress; confirmed source deletion, including its
  dependent generated courses. Course name/reference maps to the backend's
  `course_id`; the expandable activity reference defaults to `upraise-learning`.
  Production callers must enter identifiers allowed by their gateway.
  Selecting PowerPoint exposes speaker-note and hidden-slide options, both off
  by default. Original slide numbers and native structure are retained alongside
  rendered slide checks. Teacher review offers original slide previews on request.
  Generation, revisions and questions preserve slide references. See
  [PowerPoint ingestion](../backend/docs/powerpoint-ingestion.md) for content
  support, source checks and preview retention.
- **Generation:** selected source supplies course/activity/document identifiers;
  title, language, voice and word/window settings map to A3 v2. Difficulty
  checkboxes default to all configured levels and require at least one; only the
  selected levels are requested as `levels` and receive text/audio. Choices
  survive section navigation and unchanged retries reuse the request key.
  Material marked **Needs source review** can also be selected: explicitly check
  **Generate from the available content** to create a draft using what was ingested.
  Missing/rejected visual details remain excluded, and source-coverage warnings
  stay visible in teacher review. The choice resets for a different source/new
  course and is recorded as `allow_incomplete_source: true` in the generation request.
  A4 reports actual
  progress every five seconds, pausing on errors. A5 displays real generated
  text, sources and automated review evidence. A7 revises a selected part/level,
  optionally across all aligned levels; its processing replaces the existing
  course. Cancellation uses A6 `CANCEL`. No A6 `RECEIVED` is sent automatically.
- **Review:** a dedicated course collection uses `GET /v1/lesson-reviews` and
  `GET /v1/lesson-reviews/{job_id}` to read retained teaching text independently
  of temporary audio. The **Teacher review** course selector opens a course with
  counts of sections needing review, recorded findings and total sections at the
  selected difficulty. **Needs review** shows sections with findings, missing
  verdicts or a non-passing automated review; **All sections** also includes those
  without findings. Tags and finding previews identify what needs checking.
  Opening a section shows its original saved text, expanded reviewer notes, source
  references and available audio. V1 audio also works without a separate spoken
  script. Previous/next navigation follows the selected filter; choosing an
  unflagged part from the part selector opens All sections. Course switching and
  pagination preserve per-part feedback and stop obsolete audio responses.
  One entry per course remains searchable by title/reference, with pagination and
  custom difficulty levels. Counts and filters are computed from existing review
  data; browsing never regenerates a course or submits feedback automatically.
  No backend API or generation behavior changes are required. Feedback stays in
  tab memory per course/part while browsing. A7
  updates the selected part or its counterparts across levels, with inline
  progress, manual status recovery, and refreshed text after success. No learner
  session is required. Expired/received output and incomplete generations remain
  readable. A completed course with a saved persistent package remains revisable
  after temporary cleanup. Unsaved expired/received output and incomplete
  generations cannot be revised through A7.
- **Learning:** paginated A8 results group variants into one course. Available
  courses show a generation-version badge beside their title, including existing
  courses. Saved teaching metadata distinguishes V1, V2 and V3; a recorded V2.8
  policy shows V2.8. Missing/unsupported version metadata shows **Version unknown**,
  and a V2 course without one consistent minor policy shows V2. A3 v2 is an API
  format and is never used to infer the teaching version. No course regeneration
  is needed.
  Available levels come from the actual A5 bundle, including custom names. B1 opens the
  selected session; A5 text, result hash and checksum-verified WAV are checked
  before explicit playback actions. B4 captures the exact browser playback
  position, displays real teacher text/audio and any bounded playback instruction,
  and resumes the same part. Questions accept typed text or **Record a voice question**.
  Recording requires microphone permission in a supported browser on localhost or HTTPS.
  Speak in the course language, stop recording (automatic at 60 seconds), listen to
  the preview, then **Send voice question**, or discard it and type instead.
  AudioWorklet captures mono audio and the browser encodes 16 kHz, 16-bit PCM WAV;
  B4 receives it as multipart through the same authenticated bridge. No new backend
  endpoint is needed. The backend's existing Amazon Transcribe configuration and
  `transcribe:StartStreamTranscription` permission are required for voice input.
  Voice retry keeps the same request ID, WAV bytes, part and paused position.
  Recordings stay in tab memory and are released after success or discard; active
  microphone capture stops on cancellation, navigation, tab hiding, session expiry
  or lesson end. The conversation labels these turns as voice questions; B4 does
  not expose the recognized question transcript. Answer audio exposes Listen/Pause/Resume, Stop and
  Replay controls. **Ask another question** stops the previous answer, focuses an
  empty question box and keeps the same session, part and paused position. The
  conversation displays all questions and answers from the current interruption
  in order; only the newest answer keeps audio controls. Each
  new question gets a new request ID; retries keep their original ID. **Continue
  learning** releases answer audio and clears the visible transcript before
  resuming the lesson. The next interruption opens an empty chat. The backend
  retains complete teaching exchanges only in lesson-session RAM, and uses the
  latest four previous pairs (up to 2000 characters per message) as model context.
  No chat is saved to PostgreSQL, browser storage or the course archive.
  B5 closes completed/stopped/expired sessions and
  clears temporary question/audio data, with explicit retries after failures.
- Upload, generation, B1 and B4 retries preserve their appropriate request
  identities in tab memory. A7 has no backend idempotency guarantee, so an
  uncertain submission is not automatically retried. Late answers and media
  responses are discarded after navigation/session changes.
- Backend output remains **DRAFT**. Automated reviews are supporting evidence,
  not human approval. Compose saves completed narration in the root
  `course_archive/` directory; saved courses remain playable/revisable after the
  temporary 24-hour files expire. Older missing audio cannot be restored from
  PostgreSQL text alone. See [archive storage](../backend/docs/lesson-archive.md). B4 grounding
  warnings remain visible. No cognitive-load measurements or certificates are
  invented.

All displayed content comes from HTTP APIs. There is no sample fallback, direct
database access, AWS access, browser storage, analytics, or persistent learning
progress. Page reload loses local job tracking/retry state; available documents
and courses reload from the backend. Explicitly close a session before reloading;
page exit also attempts best-effort B5 cleanup, with backend TTL as the fallback.

## Authentication and connection settings

The UI detects the backend's explicit `local-test` response header for development
draft previews. It does not enable local mode or weaken backend authentication.
Production uses the existing OAuth/mTLS gateway. Set credentials on the server:

| Environment variable | Meaning |
| --- | --- |
| `BACKEND_BASE_URL` | HTTP backend or HTTPS gateway URL |
| `FRONTEND2_BACKEND_TOKEN` | Service OAuth bearer token |
| `FRONTEND2_CLIENT_CERT` | PEM client certificate file path |
| `FRONTEND2_CLIENT_KEY` | PEM private-key file path |
| `FRONTEND2_KEY_PASSWORD` | Optional private-key password |
| `FRONTEND2_CA_CERT` | Optional private CA PEM file path |
| `FRONTEND2_ACTIVITY_ID` | Default authoring activity reference |
| `UPRAISE_LESSON_LEVEL_PROFILES` | Same optional profile configuration as API/workers; supplies selectable generation levels |

TLS verification is always enabled; credentials never follow redirects. In Docker,
mount gateway certificate/key files read-only through a local Compose override and
set their paths inside the container. Neither server invocation nor Docker copies
credential files from the repository. Environment values are read at startup;
restart only `frontend2` after changes. Host-run Python does not automatically
load root `.env`; Compose performs its normal environment interpolation.

A signed **Middleware lesson session token** can be entered in the start dialog.
It is required outside local development, must bind the exact generation, level
and result hash, and attests external human approval. It stays in tab memory and
is only forwarded to the backend's session APIs. No forged approval or gateway
headers are supplied. Service access tokens/private keys remain server-side.

This is a local POC server. Browser traffic uses same-origin `/api/` endpoints;
the bridge checks Host/Origin and a per-server UI nonce, restricts endpoint and
header forwarding, and exposes only the static UI assets. It is not a public
multi-user authentication gateway.

The [local authentication workspace](tests/auth_demo/README.md) adds sign-in
before the same UI on port 8502 and exercises production JWT and mTLS checks
with two test users. Its Compose project has its own database and test course.

## Verification

```powershell
python -m unittest discover -s frontend2/tests -p test_server.py -v
npm.cmd --prefix frontend2 ci --ignore-scripts
npm.cmd --prefix frontend2 test
```

Node.js is needed only for development tests; jsdom is a pinned dev dependency.
The Python bridge tests use a disposable local HTTP server. DOM tests exercise
real API request/response shapes with fake providers; neither suite uses live
uploads, model calls or deletion.

On 2026-10-14, bridge/UI tests, JavaScript syntax, Compose configuration/container
build, and a read-only check of the live material/catalog/result/audio APIs passed.
Visual browser QA and audible playback remain unverified: browser-control surfaces
were unavailable. Generated text is escaped and displayed faithfully; a rich
Markdown/LaTeX typesetter is not included in this frontend.

## Source map

- `serve.py`: static server, route-limited HTTP bridge and server-side TLS/auth.
- `api.js`: same-origin client, request errors, file/audio hashing.
- `ui.js`: local SVG presentation helpers and text escaping.
- `app.js`: authoring, progress, revision, learner session and playback views.
- `styles.css` / `index.html` / `favicon.svg`: approved UPRAISE visual design.
- `tests/`: maintained bridge and DOM interaction regressions.

Backend contracts: [A3 v2](../backend/docs/a3-v2.md),
[A7](../backend/docs/a7.md), [A8](../backend/docs/a8.md),
[Teacher review](../backend/docs/lesson-review.md),
[B1](../backend/docs/b1.md), [B4](../backend/docs/b4.md),
[B5](../backend/docs/b5.md).
