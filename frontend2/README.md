# UPRAISE client interface

The client-facing UPRAISE interface has three sections: **Upload material**,
**Generate course**, and **Start learning**. It connects to the existing backend
through a small Python HTTP bridge. The original Streamlit `frontend/` remains
available independently.

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

- **Material:** PDF/Word/UTF-8 CSV upload through A1; browser SHA-256; real A2
  document library and progress; confirmed source deletion, including its
  dependent generated courses. Course name/reference maps to the backend's
  `course_id`; the expandable activity reference defaults to `upraise-learning`.
  Production callers must enter identifiers allowed by their gateway.
- **Generation:** selected source supplies course/activity/document identifiers;
  title, language, voice and word/window settings map to A3 v2. Difficulty
  checkboxes default to all configured levels and require at least one; only the
  selected levels are requested as `levels` and receive text/audio. Choices
  survive section navigation and unchanged retries reuse the request key.
  A4 reports actual
  progress every five seconds, pausing on errors. A5 displays real generated
  text, sources and automated review evidence. A7 revises a selected part/level,
  optionally across all aligned levels; its processing replaces the existing
  course. Cancellation uses A6 `CANCEL`. No A6 `RECEIVED` is sent automatically.
- **Learning:** paginated A8 results group variants into one course. Available
  levels come from the actual A5 bundle, including custom names. B1 opens the
  selected session; A5 text, result hash and checksum-verified WAV are checked
  before explicit playback actions. B4 captures the exact browser playback
  position, displays real teacher text/audio and any bounded playback instruction,
  and resumes the same part. B5 closes completed/stopped/expired sessions and
  clears temporary question/audio data, with explicit retries after failures.
- Upload, generation, B1 and B4 retries preserve their appropriate request
  identities in tab memory. A7 has no backend idempotency guarantee, so an
  uncertain submission is not automatically retried. Late answers and media
  responses are discarded after navigation/session changes.
- Backend output remains **DRAFT**. Automated reviews are supporting evidence,
  not human approval. Audio availability follows the existing retention policy
  (normally 24 hours); durable backend text is not an audio archive. B4 grounding
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
[B1](../backend/docs/b1.md), [B4](../backend/docs/b4.md),
[B5](../backend/docs/b5.md).
