# UPRAISE Ingestion Orchestrator and API Testbench

This Streamlit interface is a development/testing tool in frontend/, separate
from the final product. It sends HTTP requests to the backend and has no direct
database, PDF processing or AWS access. It preserves the backend API contracts.

The ingestion workspace uses A3 v2 (all aligned difficulty levels); the individual
Generate lesson API tool also supports v1. Learning groups variants by lesson
with a separate difficulty selector; the standalone B1 tool lists each variant
separately; A5 and A7 offer a level
selector for a bundle opened by job ID. Text lengths and audio durations may vary,
while the chunk count/order and teaching-unit IDs remain aligned across levels.

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

- **Ingestion workspace — A1–A8**: the default page simulates the orchestrator's
  authoring workflow through the real HTTP APIs. Four steps keep the material,
  source documents, generation inputs and job results connected:

  1. **Ingest material — A1** accepts a PDF or signed Moodle URL and computes
     the uploaded PDF's SHA-256 for `content_hash`. Suggested course/activity IDs
     and source language sit in the form's **Course and activity** expander.
     Document/job UUIDs come from the API and carry into draft generation.
  2. **Manage material — A2** contains only **Show ingested material** and
     **Remove material** panels. **Show all ingested material** loads all undeleted
     documents accessible to the caller across authorized courses. Remove by
     selecting a loaded document or entering its UUID. Status and progress are
     shown in the material table; progress uses two decimal places, e.g. `12.35%`.
     Removing a document also deletes every generated lesson that used it,
     including all levels, languages and revisions. **Delete an entire course**
     inside Remove material takes a course ID and explicit confirmation, then
     calls `DELETE /v1/courses/{course_id}` to remove its vectors and all generated
     text/SSML/plans/temporary lesson output. Other owners/courses are preserved.
     Qdrant material and saved lesson text have no automatic expiry; A6 receipt
     and the 24h voice/JSON TTL are separate from this permanent-content deletion.
  3. **Draft lesson** loads the current source table when opened. Its generation
     box fixes the version to **A3 v2 · All aligned levels** and provides a single
     **Course material** selector. Course/activity/document IDs fill automatically
     from that selection and are read-only; A1 inputs stay separate. Set the
     editable generation parameters, then click **Generate lesson · A3**.
     Defaults are 5 source pages, 200 target words and a 500-word maximum.
     Output language/voice and optional push callback remain editable. English
     (`en`) and Italian (`it`) have a Polly voice dropdown showing accent and
     gender; locale tags such as `en-GB` narrow the choices. Language changes
     refresh the choices without submitting a generation. Defaults: Joanna for
     English, Bianca for Italian. Other languages retain manual voice entry.
     The same picker is available in the standalone **Generate lesson** tool.
     `voice_picker.py` contains the neural catalogue verified with Polly
     DescribeVoices in `eu-central-1` on 2026-10-13 (23 English, 2 Italian).
     Reverify the catalogue when changing the engine/region or adding voices;
     the backend validates every selected voice. Failed or
     cancelled sources cannot be selected; A3 waits for pending ingestion.
     **Refresh source status** updates the table with progress percentages.
     Selection changes prepare a new retry key; navigation and refresh failures
     preserve the same selected source/key. The request-sequence panel is hidden
     on this step. The individual Generate lesson API tool still supports v1/v2.
  4. **Jobs & results** tracks all accepted/imported jobs through A4, discovers
     existing drafts through paginated A8, and reviews A5 text, source evidence,
     automated findings and checksum-checked audio per level/chunk. The visible
     **Course** selector automatically lists available generations from all
     authorized courses, independently of A1/A3 form IDs. Select **Course**, then
     **Level** and **Teaching chunk**, then
     click **Download voice**. This single button fetches just that chunk's WAV on
     click, verifies its SHA-256, and downloads it without rerunning the page.
     JSON and bulk ZIP export controls are omitted from this workspace.
     **Refresh courses** reloads A8 and **Load more courses** handles pagination.
     Manual UUID import, job polling and receipt controls sit in **Advanced job tracking**.
     A7 feedback creates a separate tracked revision job. A6 CANCEL/RECEIVED are
     explicit; RECEIVED asks you to confirm the required output has been saved
     or the failed/material job inspected. Collection never sends A6 automatically.

  A1/A3 retry keys survive navigation. Changed inputs with the same attempted key
  are blocked: prepare a new document/generation or restore the original request.
  Changing course/activity prepares fresh inputs while retaining job history;
  changing connection credentials clears the workspace. On the other steps, the
  last 100 HTTP calls can be exported as a request sequence with request bodies, IDs, status and timing;
  credentials and signed Moodle/callback URLs are omitted. Workspace data and
  downloads are session-only. This simulates the API caller; it requires a running
  backend for real ingestion/generation. No offline provider simulation is added.
- **Learning — A8/A5 + B1/B4/B5**: lists completed lessons whose temporary
  outputs are still available and whose source documents remain active. Select
  an **Available lesson** and **Difficulty level**, then **Start lesson** creates
  B1 before displaying its first chunk's text and voice. Settings allow local
  questions consent (enabled by default) or a signed Middleware session JWT.
  A3 v2 levels share one lesson choice; A7 revisions remain separate choices.
  **Next chunk** advances; **Replay voice** restarts the current chunk.
  A custom Streamlit component plays checksum-verified WAV bytes and handles
  **User interruption** in the browser: pause first, capture exact currentTime,
  then open the text question box. **Send question** uses B4 with the same B1
  session, current chunk and captured playback offset. Replies display text and
  voice; **Retry question** preserves the request ID and payload.
  **Back to course** rechecks A5 and resumes the same chunk at the saved position.
  Explicit teacher REPLAY/PAUSE/SET_SPEED instructions override playback as requested.
  Browsers that block autoplay show native Play controls. Positions stay in RAM;
  no continuous telemetry or browser storage is introduced.
  **Finish lesson** sends B5 `COMPLETED`; **End lesson** sends
  `STOPPED_BY_LEARNER`. Both clear interruption replies
  and stop playback. If closure fails, **Retry session end** preserves the same
  session/reason without retaining its answer audio. A3 course WAVs are preserved.
  Session expiry stops playback and requires an explicit fresh start. Unavailable
  A5 output closes the session with ERROR. Next chunk is player navigation; B2's
  cognitive-load measurement remains in the dedicated test page.
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
- **Revise chunk (A7)**: load a ready lesson by catalog or job ID, select its
  teaching chunk and describe the teacher's requested changes. Language, level
  and voice are inherited. Submission creates a new complete lesson version;
  an inline panel shows its A4 status, processing step and percentage, refreshing
  every 5 seconds until DONE/FAILED/CANCELLED. HTTP/network errors pause tracking;
  **Refresh revision status** resumes it. **Open revised lesson** explicitly loads
  the new version once completed; the selected edited part is retained in the
  standalone tool. The same panel appears in workspace A7. Processing status
  remains available, and Learning lists completed revisions. Loading a lesson
  (A5 GET/200) is distinct from submitting a revision (A7 POST/202); HTTP details
  have separate labels. No automatic submission retry; A7 request idempotency is deferred.
  See [A7](../backend/docs/a7.md).
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
  player. B3/B6 remain reserved. See [B1](../backend/docs/b1.md).
- **Chunk end (B2)**: enter the active B1 session, lesson and completed chunk IDs
  and a cognitive-load test measurement between 0 and 1. B1 session/lesson fields
  prefill when available. An explicit click sends B2 and displays PROCEED at or
  below 0.5, HIGH_COGNITIVE_LOAD above 0.5. Without Telemetry consent in B1, B2
  always proceeds. The page does not move the Learning player automatically.
  Credential, B1 session or input changes clear previous results; B5 clears
  matching cached measurement/decision. See [B2](../backend/docs/b2.md).
- **Learner interrupt (B4)**: start B1 with Questions consent, load A5 teaching
  chunks, choose the interrupted chunk, and submit free text or a PCM WAV recording.
  Displays/plays inline answer text/audio and source references, or displays a
  player-control instruction for Middleware to apply. Signed requests reuse the
  B1 session JWT, with manual ID/JWT entry available. Stable request IDs support
  explicit retries; navigation/reruns never resubmit. Backend RAM restart/expiry
  ends availability. This separate API test page remains available alongside
  the integrated interruption panel in Learning.
  See [B4](../backend/docs/b4.md) for limits, consent and temporary storage.
- **End session (B5)**: enter the B1 session UUID and choose its end reason.
  Production also supplies the matching masked session JWT. Explicit submission
  returns empty 204 and clears matching frontend B1/B4 state and answer audio.
  Retry with the same session ID/reason after a timeout. No A2/A6 action occurs;
  generated course text/audio and source material retain their existing policies.
  See [B5](../backend/docs/b5.md).
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
- **Health — C1/C2/C5**: explicit GET /health and /ready checks, plus optional-course
  GET /v1/status. Shows feature states/reasons including UNKNOWN. Credential/course
  changes clear old results; checks do not invoke AI. **Check versions** calls
  GET /v1/version for deployed model/prompt/pipeline/policy metadata.
  See [C1/C2](../backend/docs/c1-c2.md) and [C5](../backend/docs/c5.md).
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
