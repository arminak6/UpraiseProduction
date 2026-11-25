# Local API testing

On 2026-10-02 the user explicitly approved a local testing mode so the Streamlit
interface can exercise A1/A2 before Moodle OAuth/mTLS integration is configured.
This is a deployment setting, not a new API or a change to the document pipeline.

## This workstation

The ignored root `.env` now contains:

```dotenv
UPRAISE_ENVIRONMENT=development
UPRAISE_LOCAL_TEST_MODE=true
```

Start/rebuild from the project root:

```sh
docker compose --profile tools up --build -d --wait --wait-timeout 180
```

Open http://localhost:8501. Leave bearer token and mTLS inputs empty, use
**Ingest material** for A1, **Processing status** for A4, and **Manage material**
for A2 list/delete. **Generate lesson** provides A3; Processing status offers
A5 DRAFT result/audio and A6 cancellation/receipt. Submit known A1 document IDs;
no re-ingestion is required. Leave callback_url empty for pull-only tests.
Outbound callbacks still require mTLS/OAuth deployment configuration in local mode.
Course/activity/source metadata and PDF support limits still
apply. AWS/PDF extraction, PostgreSQL, Qdrant and audit run normally.

The backend assigns the fixed `local-test-client` identity, allows any course ID
entered for these tests, and grants only documents:read, documents:write,
lessons:write, jobs:read, jobs:write and sessions:write. Client ownership remains enforced: local mode does not expose or
delete documents belonging to another Middleware client. Requests must arrive
from a loopback/private network peer (including the Compose frontend container),
using the actual socket address, not forwarded headers. The existing API and
Streamlit port mappings bind to 127.0.0.1.

C3 administration needs an additional explicit development-only opt-in:
`UPRAISE_OPERATIONS_LOCAL_ADMIN=true`. Its default is false, so ordinary local
testing does not grant `admin:operations`. See [C3](c3.md) for the runtime-control
contract, additive migration and rollout. This option is rejected outside
development local test mode; production uses an operator OAuth scope.

Streamlit displays a local testing notice for its configured backend. Actual
backend responses include `X-UPRAISE-Auth-Mode: local-test`; the interface also
displays that confirmation with each response. Health JSON stays unchanged.
Changing the Streamlit backend URL does not enable local mode on that server.

B1 `POST /v1/sessions` also supports JWT-free local DRAFT previews, with optional
body session UUID/consent simulation. Its response omits capabilities;
cognitive-load and streaming APIs remain reserved. See
[B1's one-line curl and lifecycle](b1.md). B4 additionally requires Questions
consent on that session and an exact chunk ID from A5; the dedicated frontend
page tests text/WAV interruptions. Learning also offers **Interrupt / ask teacher**
with automatic current-chunk selection and an integrated B1/B4 question panel.
See [B4](b4.md).

## Direct curl with a JWT

Create a test client secret from the project root before starting the JWT test
stack. This generates a random secret if the file is absent. It prints the
secret for the person setting up this local test.

```powershell
New-Item -ItemType Directory -Force codex_helper/jwt-test | Out-Null
if (-not (Test-Path codex_helper/jwt-test/client.env)) {
    "UPRAISE_JWT_TEST_CLIENT_SECRET=$([guid]::NewGuid().ToString('N'))" | Set-Content -Encoding Ascii codex_helper/jwt-test/client.env
}
Get-Content codex_helper/jwt-test/client.env
docker compose -f compose.jwt.yaml up -d --wait
```

Both Compose setups use port 8000. Stop the regular Compose backend before
starting the JWT stack if it is already running. The optional file enables
the local token endpoint on port 8001. `auth-demo-middleware` is the test client
ID. Use the generated value after `=` as its secret.

```powershell
$secret = Read-Host "Test client secret"
$jwt = (curl.exe -fsS -u "auth-demo-middleware:$secret" -d "grant_type=client_credentials&scope=documents:read" http://127.0.0.1:8001/oauth/token | ConvertFrom-Json).access_token
curl.exe -i -H "Authorization: Bearer $jwt" http://127.0.0.1:8000/v1/documents
```

These curl commands need only the URLs and test client credentials. The issuer
returns a JWT valid for five minutes. For local administrators, the JWT stack
also maintains a token inside the backend at `/tmp/upraise-access-token`.

A valid token returns `200`. A missing or invalid token returns `401`. Request a
fresh token if the current one expires. Use `demo-course` for authoring requests.
The stack has its own PostgreSQL and Qdrant databases and runs without processing
workers. It publishes only to localhost. This test mode requires development and
verifies the bearer JWT, scopes, and course access. It accepts curl directly over
HTTP. Production calls continue to use the configured mTLS gateway.

## Production/default authentication

Both code and committed Compose/example defaults are:

```dotenv
UPRAISE_ENVIRONMENT=production
UPRAISE_LOCAL_TEST_MODE=false
```

Local mode requires both explicit development values. Enabling it while leaving
environment=production refuses application startup. Development alone does not
disable authentication; missing production identity/gateway settings still return
503 INGRESS_NOT_CONFIGURED. Production JWT signature/expiry/scope/course checks
and trusted mTLS-gateway enforcement are unchanged.

To return this workstation to production authentication, set those two production
values in its ignored `.env`, configure actual OAuth/mTLS settings, and recreate
backend/frontend. Local test documents keep their testing client identity; they
are not silently reassigned to a real Moodle integration. Do not deploy this
workstation's ignored development `.env` to a production environment.

Tests run with production defaults explicitly pinned, then exercise the real
local-mode branch using isolated storage and synthetic documents:

```sh
docker compose run --rm --no-deps --user root -e UPRAISE_ENVIRONMENT=production -e UPRAISE_LOCAL_TEST_MODE=false -v ./backend/src:/app/src:ro -v ./backend/tests:/tests:ro backend uv run --group dev pytest /tests -q -p no:cacheprovider
```
