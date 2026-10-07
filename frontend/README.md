# UPRAISE Learning and API Testbench

This Streamlit interface is a development/testing tool in frontend/, separate
from the final product. It sends HTTP requests to the backend and has no direct
database, PDF processing or AWS access. It preserves the backend API contracts.

## Start

From the project root:

```sh
docker compose --profile tools up --build -d --wait --wait-timeout 180
```

Open <http://localhost:8501>. To rebuild only the interface when the backend
is already running:

```sh
docker compose up --build -d --no-deps --wait frontend
```

The frontend uses the optional tools Compose profile. Normal backend startup
does not start it. Stop just the interface with docker compose stop frontend;
stop the entire development stack with docker compose --profile tools down.

BACKEND_BASE_URL defaults to http://backend:8000 inside Compose, and
http://localhost:8000 for a host-run Streamlit session. The sidebar can select
an HTTP backend or HTTPS gateway. Requests run on the Streamlit server, so
localhost in Docker refers to that container; use host.docker.internal for a
service running on the Windows host. frontend/.env.example is documentation,
not an automatically loaded file.

## Tools

- **Learning — A8 + A5**: the default page lists completed lessons whose temporary
  outputs are still available and whose source documents remain active. Select
  an available course, then **Start course** for its text and teacher voice.
  Previous/Next select parts; the audio controls pause/resume; Replay restarts
  the audio; Finish/Restart and Back to courses control session-only progress.
  Read aloud can be disabled. The selector includes language, explanation level
  and creation time so separate generations can be distinguished. It also accepts
  a job UUID when automatic listing is unavailable.
  **Interrupt / ask teacher** stops lesson audio and opens a question panel for
  the current part. Enable questions in local preview (or supply the signed
  Middleware session JWT), then **Ask teacher** sends text or a WAV recording.
  B1 starts on the first question; later messages reuse that session and B4
  automatically receives the current chunk. Replies display text, audio and
  sources; **Retry question** preserves the request ID. **Continue course**
  rechecks A5 and resumes this part, applying any replay/speed instruction.
  Playback-position inputs are omitted; Continue replays the current part from
  its beginning. Expired sessions require an explicit new session.
  No generation or A6 receipt happens automatically. Each explicit playback action
  rechecks A5, and changing the backend/authentication clears cached playback.
  Ready means a DRAFT preview requiring human approval. Expired/acknowledged jobs
  are excluded: the PostgreSQL text archive does not provide permanent audio playback.
  See [A8's contract](../backend/docs/a8.md).
- **Generate lesson — A3**: ordered document UUIDs, lesson/course/activity,
  output language/Polly voice, configurable page windows, word target and explanation
  level. Optional MW callback requires outbound OAuth/mTLS configuration.
  Submission returns job_id and remembers A4 Location; the retry key survives
  navigation. Start a new lesson generation to request fresh output.
- **Start session (B1)**: choose an available A8 lesson, then **Start session**.
  Displays the B1 HTTP response, session UUID, AI notice, language/level
  and expiry. **Retry session start** reuses the same local UUID
  and input; **New local session** prepares a fresh UUID without sending a request.
  Selection/consent/credentials changes clear the previous response. Manual lesson
  details work when catalog access is unavailable. Local preview allows optional
  questions/telemetry consent simulation. Signed mode accepts a password-masked
  Middleware session JWT in addition to the sidebar service token/mTLS settings;
  it sends no body session/consent claims. Requests only run after explicit clicks.
  The page initializes backend RAM state; Learning remains the separate text/voice
  player. B2/B3/B5/B6 remain reserved. See [B1](../backend/docs/b1.md).
- **Learner interrupt (B4)**: start B1 with Questions consent, load A5 teaching
  chunks, choose the interrupted chunk, and submit free text or a PCM WAV recording.
  Displays/plays inline answer text/audio and source references, or displays a
  player-control instruction for Middleware to apply. Signed requests reuse the
  B1 session JWT, with manual ID/JWT entry available. Stable request IDs support
  explicit retries; navigation/reruns never resubmit. Backend RAM restart/expiry
  ends availability. This separate API test page remains available alongside
  the integrated interruption panel in Learning.
  See [B4](../backend/docs/b4.md) for limits, consent and temporary storage.
- **Lesson result/audio — A5**: on Processing status, fetch DRAFT JSON and
  authenticated chunk audio, play or download the WAV. Download needed artifacts
  before A6 RECEIVED removes the temporary lesson copy. Expiry defaults to 24 hours.

- **Ingest material — A1**: PDF multipart upload or signed Moodle URL, course,
  activity, source-language, Moodle file ID, source hash/revision and optional
  version. Uploaded content_hash can be filled from local SHA-256 as a
  convenience; the backend does not verify declared hashes. Any filename/MIME
  can be submitted for testing; only readable PDFs are currently processed.
  Acceptance returns document_id and ingestion job_id, plus the A4 Location header.
- **Processing status — A4**: automatically remembers A1's Location header.
  Refresh manually or poll every five seconds. Polling stops after DONE/FAILED/CANCELLED
  or pauses on an HTTP/connection error. A different status URL clears the
  previous result. It accepts a status path or a job UUID; absolute URLs must
  use the configured backend origin.
- **Job actions — A6**: on Processing status, select CANCEL for queued/running
  work or RECEIVED for DONE/FAILED output, then explicitly send the request.
  The reason is a query parameter; success is empty 204. A pending cleanup 503
  can be retried with the same ID/reason. No action resubmits on navigation.
  Source PDFs and acknowledged ingested material are retained; see
  [A6's contract](../backend/docs/a6.md).
- **Manage material — A2**: GET /v1/documents with course_id shows status/version
  and active chunk counts. Delete by document UUID sends the unchanged DELETE
  endpoint and displays its empty 204 response, then refreshes the course list.
  A1 pre-fills the latest accepted document/course. Deletion requires an explicit
  button click and does not resubmit on navigation. Invalid UUIDs are rejected
  locally; failed deletes retain their HTTP response for retry.
- **Health — C1**: actual GET /health response and timing.
- **API explorer**: load live OpenAPI and send GET/POST/PUT/PATCH/DELETE requests
  with query parameters and JSON bodies, including negative tests. Use A1's
  dedicated form for multipart delivery.

A1's JSON contains document_id and job_id. The interface also follows the
existing Location status URL. HTTP status,
elapsed time, actual JSON/text and selected response headers are displayed.
POST is sent only by an explicit submit click; navigation does not resubmit.
Idempotency-Key survives page changes. Reuse it for a network retry; click
Start a new submission to create a different key for intentional new work.

## Authentication

Enter an OAuth bearer token in the sidebar. For the real mTLS gateway, select
an HTTPS base URL and upload PEM client certificate/private key, plus an
optional encrypted-key password and private CA certificate. TLS verification
is always enabled. Requests never automatically follow redirects or send
credentials to another origin through an A1 status URL.

The user-approved local testing mode is enabled on this workstation through
ignored root .env. Leave bearer token and mTLS fields empty to use Learning and the API tools.
The page displays a local-mode notice, and responses confirm the backend mode.
See ../backend/docs/local-testing.md for setup and return to production settings.
Production authentication is the default. Without real OAuth/mTLS configuration,
production requests return 503 INGRESS_NOT_CONFIGURED; the interface displays
the explanation. See ../backend/docs/a1.md for integration and
../backend/docs/a2.md for list/deletion semantics and retention. The frontend
never supplies forged gateway assertions.

Tokens, signed URLs and uploaded documents are held in the Streamlit session,
not saved to project files or application logs. SSLContext needs certificate
filenames: PEM files are briefly loaded through a private temporary directory,
then deleted before sending the request. The key and token are omitted from
connection object representations and response-header displays. Clear session
data removes credentials, inputs and results. The server itself listens only
through Compose's localhost port mapping.

Network timeouts are configurable (default 180 seconds per operation;
connect 10 seconds); health uses 5 seconds and status checks 15 seconds.

## Checks and dependencies

Streamlit 1.64.0 and httpx 0.28.1 are pinned in requirements.txt. Docker uses
Python 3.12.14; no host Python is required. The maintained interaction/client
checks use standard unittest and Streamlit AppTest, without another dependency:

```sh
docker compose run --rm --no-deps -v ./frontend:/workspace:ro frontend python -m unittest discover -s /workspace/tests -v
```

The Docker context includes requirements.txt and the frontend application modules;
tests, documents and local credentials are excluded.

References: [Streamlit AppTest](https://docs.streamlit.io/develop/api-reference/app-testing/testing-element-classes),
[Compose profiles](https://docs.docker.com/compose/how-tos/profiles/).
