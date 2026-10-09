# UPRAISE AI Module

A1 PDF ingestion, A2 list/delete, A3 draft lessons, A4 status, A5 result/audio, A6 control,
A7 chunk revision/audio regeneration, A8 ready-lesson listing, B1 lesson start, B2 chunk-end decisions, B4 learner interruption, B5 session end,
C1 health/readiness and C2 feature status are implemented.
The API accepts teaching material from authenticated Middleware, stages it
persistently, and returns HTTP 202 with document_id and job_id. The Location header
identifies the original A4 job-status URL; durable internal jobs remain. A separate
worker runs the reviewed Uprise/v2 extraction method and stores course chunks
and Titan embeddings in Qdrant. PostgreSQL stores operational job state and
separate audit records; document text is not duplicated there.
Finalized A3 lesson text is stored separately in PostgreSQL
`prepared_lesson_chunks`, with provenance, review and version metadata. All
chunk text is committed before speech synthesis, and exact SSML request parts
are saved before each TTS call. This follows the user's 2026-10-06 request to
retain the original text used for audio, as in Uprise/v2.

The original Word draft remains the API baseline, with the user-approved
extensions recorded in [api-baseline.md](backend/docs/api-baseline.md).
The Word file was supplied again on 2026-10-06 and checked directly. The supplied
architecture diagram is retained as conversation context. A/B/C grouping and
all reserved endpoints remain unchanged. A8 is the user-approved 2026-10-07 extension
for listing ready lessons; see [A8's contract](backend/docs/a8.md).
See [A3's Version 3 workflow and callback setup](backend/docs/a3.md) and
[A5 pull downloads, auditing and TTL](backend/docs/a5.md).
See [A2's contract and deletion recovery](backend/docs/a2.md).
See [A6's cancellation and acknowledgment contract](backend/docs/a6.md).
See [A7's teacher-feedback revisions and matching audio](backend/docs/a7.md).
See [B1's temporary sessions, JWT/consent contract and local curl](backend/docs/b1.md).
B1 opens process-local learner state for one selected A3 generation; restarts
clear sessions. Its default 30-minute TTL is capped by lesson/token expiry.
Local testing allows draft previews; production requires Middleware's signed
approval assertion. B4 uses LangGraph semantic routing, exact prepared teaching
text, scoped Qdrant evidence and inline speech. Replies use original v2 teacher
responses with safety/language checks. Factual grounding verification is temporarily
disabled: `grounded=null`, with contextual source references. Reply text is the
input to its speech; see [B4](backend/docs/b4.md). Its replacement is in the ignored
`FUTURE_UPDATES.md` backlog.
B5 clears learner history and interruption audio and records session_ended;
course text/audio and source vectors keep their existing retention. See [B5](backend/docs/b5.md).
B2 accepts lesson_id/chunk_id/cognitive_load_score and returns a synchronous
PROCEED/HIGH_COGNITIVE_LOAD decision using the test threshold 0.5. Telemetry
consent is required for a high-load reply; the latest score stays in RAM only.
See [B2's contract and local curl tests](backend/docs/b2.md).
B3/B6 remain reserved. Existing 24-hour lesson retention remains.
On 2026-10-02 the user explicitly removed the A1 Ingestion Guard and job_id
from its response body. On 2026-10-06 the user restored job_id in that body,
matching the Word specification. A1 retains processor readability/resource limits and
OAuth/mTLS INGRESS. content_hash is unverified source metadata; actual SHA-256
is computed internally as source_hash. Current pipeline: pdf-ingestion-v1.4.
Source text/HTML and AI candidates are retained separately. Every visual source
region has its own provenance-backed crop; AI rewrites/descriptions require
structural checks and a separate source-comparison pass before acceptance.
Chunks and A4 results expose UNVERIFIED, AUTO_CHECKED or NEEDS_REVIEW quality
independently of DONE. AUTO_CHECKED records automated checks, not human approval
or guaranteed accuracy. See [source fidelity](backend/docs/a1.md#source-fidelity).

## Start

Docker Desktop/Linux containers and Compose are required. The ignored root
aws_credentials.json is mounted read-only into the backend and workers, never baked into
images. It already contains the user-provided AWS settings. A normal AWS
credential chain is supported by the Python adapter when no credential file
is configured; adjust the deployment mount when using an IAM role.

```sh
docker compose config --quiet
docker compose up --build -d --wait --wait-timeout 180
docker compose ps
docker compose logs -f backend worker lesson-worker lesson-delivery
```

Startup creates the durable job/audit schemas through a repeatable migrate
service. Backend/worker use separate non-superuser database roles. The
bootstrap service alone receives PostgreSQL administrative credentials.
The Compose password defaults are for local infrastructure; replace them for
deployment. .env.example describes the settings, including connection-string
overrides for passwords that need URI encoding.

- API docs: http://localhost:8000/docs
- Liveness: http://localhost:8000/health
- Readiness: http://localhost:8000/ready; feature status: http://localhost:8000/v1/status
  (C2 uses INGRESS and jobs:read). See [C1/C2](backend/docs/c1-c2.md) for UNKNOWN
  provider evidence, partial outages and shared API/worker operator controls.
- Optional Streamlit Learning and API Testbench: http://localhost:8501

Streamlit is a testing tool in frontend/, separate from the final product.
Start it with docker compose --profile tools up --build -d --wait.
Its default Learning page lists ready lessons through A8. Choose a course and
select Start course to read its text and play its teacher voice. Previous/Next,
audio pause/resume, Replay, Finish and Restart control session-only progress.
**Interrupt / ask teacher** stops the lesson audio and opens a text/WAV question
panel for the current part. With Questions consent (or the signed session JWT),
the first message starts B1 and sends B4; later messages reuse the session.
**Continue course** returns to this part and applies replay/speed instructions.
**Finish course** and **Back to courses** end an open question session through
B5 and clear its temporary replies. **End session (B5)** offers explicit API tests.
All lessons remain drafts requiring human review, and temporary playback expires
after the configured A5 TTL (24 hours by default).
It also provides A1 PDF/URL submission, A2 course lists/deletion, A3 generation,
A4 status polling, A5 downloads, A6 job actions, health and a general API
request panel. Enter the real OAuth token and HTTPS mTLS gateway credentials
in its sidebar for authenticated requests. See [frontend/README.md](frontend/README.md).
**Start session (B1)** lets you choose a prepared lesson, open/retry its backend
session and inspect its session ID, AI notice, language/level and expiry. Local
preview works without a JWT; signed mode has a separate Middleware session JWT
field. The Learning page remains the text/voice player.

Local Streamlit testing is enabled on this workstation through the ignored root
.env (environment=development, local_test_mode=true). Leave token/certificate
fields empty. [Local testing setup](backend/docs/local-testing.md) explains the
testing identity and how to switch back. Code/Compose defaults remain production
with local mode disabled; real deployment requires the OAuth/mTLS settings in
[the A1 integration guide](backend/docs/a1.md).
First conversion downloads Docling/EasyOCR models into the persistent cache;
allow extra time and prewarm that cache before deployment.

## Storage and recovery

A3 uses a separate generation worker and a separate delivery/expiry worker.
It accepts document IDs already submitted through A1, configurable source page
windows and output chunk word targets. It delivers only AI-generated DRAFTs,
with V3 source reviewer reports, WAV audio and viseme timings.
The copied V3 teacher reviews the draft, edits once if needed and validates the final text.
A3's beginner/intermediate/advanced explanation level guides the generator,
reviewer and editor through their system prompts. New results report
`learner_level_applied=true`; accuracy and source coverage remain required at every
level. A3 output expires after 24 hours
by default, or is removed after A6 receipt; source PDFs/vectors remain.
Optional push delivery requires the AI's outbound mTLS identity and a fresh
short-lived OAuth2 token. Pull-only A5 works without callback settings. Set up
deployment secrets and the public gateway URL as described in [a3.md](backend/docs/a3.md).
Saved lesson text/SSML remains in PostgreSQL after A6 receipt or TTL cleanup;
temporary JSON/WAV delivery still expires and A5 returns 410. Audio is not stored
in PostgreSQL. Existing jobs are not backfilled automatically. See a3.md for
operator access and archive lifecycle details.

- upraise_qdrant_data: source chunks, vectors and provenance.
- upraise_postgres_data: upraise_jobs and upraise_audit databases.
- upraise_ingestion_data: accepted PDFs, crops, native Docling JSON and prepared
  extraction/embedding checkpoints needed for recovery and inspection.
- upraise_model_cache: local extraction/OCR/formula model weights.

Job leases recover abandoned work after a worker restart. Successful preparation
is checkpointed, avoiding repeated model calls when publishing must be retried.
The worker service runs each attempt in a fresh child process. Its supervisor
enforces workload-based processing deadlines and a 12-hour total attempt limit,
and stops local processing when cancellation or lost ownership is observed.
Repeated progress and lease heartbeats do not extend those deadlines. A timed-out
attempt reports PROCESSING_TIMEOUT and retries within the existing attempt limit;
the source PDF and completed checkpoint remain available. [Worker budgets and
recovery](backend/docs/a1.md#recovery-and-limits) describes configuration and tuning.

Middleware should supply Idempotency-Key and reuse it for a network retry.
A new intentional submission receives a new document ID, matching the old
pipeline. Processing is at-least-once; external model calls are not guaranteed
exactly once. Only DONE jobs and their active_attempt may be used by future
retrieval code, and documents with a deletion request must be excluded.
Raw unfiltered Qdrant points are not the activation authority.
Processing completion also does not establish teaching accuracy. Future retrieval
must inspect per-chunk/per-element quality and source evidence; UNVERIFIED and
NEEDS_REVIEW content must not silently become verified lesson facts. Existing
v1.3 results are not upgraded automatically; re-ingest source PDFs with v1.4.

A2 deletes all document vectors by course/document filter, fences active ingestion,
and records deletion audit before 204. Durable deletion requests recover after
dependency failures. Lists combine operational status/version with active Qdrant
chunk counts. Deleted operational records and processing artifacts follow the
existing retention policy; deletion does not remove Moodle files or audit history.

A6 takes a required reason query parameter, CANCEL or RECEIVED, and returns 204
after temporary-output cleanup and audit. Cancellation immediately fences queued/
running work. A busy worker returns recoverable 503 until it stops writing files;
the supervisor stops and reaps its child before cleanup resumes. Receipt
acknowledgment retains terminal results and active vectors. Both actions preserve
source PDFs and history.

Ingestion source/artifact cleanup is disabled until an operational retention period is
agreed, preserving the old pipeline's evidence. A positive
UPRAISE_ARTIFACT_RETENTION_DAYS enables cleanup for terminal jobs/completed deletions. It does
not erase Qdrant course material, job records or audit history. Disk capacity,
backups, replication and retention are deployment responsibilities. Shared
volume storage supports the current single-host Compose deployment; multiple
hosts need shared storage or an object-storage adapter.

```sh
docker compose --profile tools down
docker compose up -d --wait --wait-timeout 180
```

Normal down retains all named volumes. down --volumes is a deliberate data
reset and is never part of validation. Existing other-project data are untouched.
Before changing the pipeline version/model settings, drain pending jobs or
retain workers for their recorded version: workers claim compatible jobs only.
When installing the supervised worker, stop the old worker before rebuilding and
restarting it so all attempts use the new execution path. This change requires no
database schema migration and does not change the pipeline signature. Timeout
defaults are starting values rather than performance benchmarks; prewarm model
weights and tune budgets with representative large PDFs before deployment.

## Layout

```text
UpraiseProduction/
  compose.yaml
  .env.example
  .gitignore
  aws_credentials.json          # local and ignored
  PROJECT_KNOWLEDGE.md
  README.md
  codex_helper/                 # ignored verification evidence/fixtures
  backend/
    Dockerfile
    .dockerignore
    .env.example
    pyproject.toml
    uv.lock
    README.md
    docs/
      api-baseline.md
      a1-design.md
      a1.md
      a2.md
      a6.md
      local-testing.md
    tests/
      test_a1.py
      test_a1_integration.py
      test_a2_integration.py
      test_a6_integration.py
      test_local_testing.py
      test_ingress.py
      test_moodle.py
    codex_helper/.gitkeep
    src/upraise_ai/
      main.py
      api/
        authoring/{__init__.py,documents.py}
        runtime/__init__.py
        operations/__init__.py
      core/{config.py,errors.py,utils.py}
      schemas/{documents.py,jobs.py}
      guards/{ingress.py,body_limit.py}
      services/
        formats.py
        chunking.py
        embeddings.py
        aws.py
        moodle.py
        pdf/{input.py,parser.py,languages.py,inline_math.py,vision.py,enrichment.py,processor.py}
      storage/{jobs.py,audit.py,files.py,vectors.py,migrate.py}
      workflows/{submission.py,documents.py,jobs.py}
      workers/{ingestion.py,supervision.py,budgets.py}
  frontend/
    Dockerfile
    .dockerignore
    .env.example
    app.py
    api_client.py
    tests/
    requirements.txt
    README.md
```

Package __init__.py files and original placeholders are omitted from the
compact tree. Only services/pdf adds nesting beyond the original API groups;
that format boundary was approved for later DOCX/CSV processors.

## Checks

```sh
docker compose run --rm --no-deps --user root -e UPRAISE_ENVIRONMENT=production -e UPRAISE_LOCAL_TEST_MODE=false -v ./backend/src:/app/src:ro -v ./backend/tests:/tests:ro backend uv run --group dev pytest /tests -q -p no:cacheprovider
docker compose run --rm --no-deps --user root -v ./backend:/workspace backend uv run --group dev ruff check --config /workspace/pyproject.toml /workspace/src /workspace/tests
docker compose run --rm --no-deps -v ./backend:/app backend uv lock --check
```

Tests use an isolated PostgreSQL job schema and Qdrant collection and remove
only their own records. Provider doubles are limited to automated tests; runtime
always uses the configured Docling/Nova/Titan pipeline. Live validation also
used synthetic PDFs, real OCR/model calls, PostgreSQL and Qdrant. Results are
recorded in codex_helper/a1-validation.md.
