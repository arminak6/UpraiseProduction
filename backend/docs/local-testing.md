# Local API testing

The development backend can run in JWT-free local test mode or issue local test
JWTs for Frontend2 and direct API calls.

## JWT-free local test mode

For JWT-free API testing, set these values in the ignored root `.env`.

```dotenv
UPRAISE_ENVIRONMENT=development
UPRAISE_LOCAL_TEST_MODE=true
UPRAISE_LOCAL_JWT_TEST_MODE=false
```

Start/rebuild from the project root:

```sh
docker compose up --build -d --wait --wait-timeout 180
```

Open Frontend2 at http://localhost:8502 for upload, generation, review and
playback. For direct calls, use the [API reference](api-reference.md).
Leave `callback_url` empty for pull-only tests. Outbound callbacks require
their mTLS/OAuth deployment configuration.

The backend assigns the fixed `local-test-client` identity, allows any course ID
entered for these tests, and enforces client ownership. Requests must arrive
from a loopback or private network peer, including the Frontend2 container.
The backend checks the socket peer address. The published API and Frontend2
ports bind to 127.0.0.1.

C3 administration needs an additional explicit development-only opt-in:
`UPRAISE_OPERATIONS_LOCAL_ADMIN=true`. Its default is false, so ordinary local
testing does not grant `admin:operations`. See [C3](c3.md) for the runtime-control
contract, additive migration and rollout. This option is rejected outside
development local test mode; production uses an operator OAuth scope.

Backend responses include `X-UPRAISE-Auth-Mode: local-test`. Frontend2 uses that
header to detect local draft-preview mode.

B1 `POST /v1/sessions` also supports JWT-free local DRAFT previews, with optional
body session UUID/consent simulation. Its response omits capabilities;
cognitive-load and streaming APIs remain reserved. See
[B1's one-line curl and lifecycle](b1.md). B4 additionally requires Questions
consent on that session and an exact chunk ID from A5. Frontend2's course
preview supports text and WAV questions in the current chunk. See [B4](b4.md).

## Direct curl with a JWT

The normal Compose backend can issue and verify local test JWTs on port `8000`.
Set these values in the ignored root `.env`.

```dotenv
UPRAISE_ENVIRONMENT=development
UPRAISE_LOCAL_TEST_MODE=false
UPRAISE_LOCAL_JWT_TEST_MODE=true
UPRAISE_JWT_TEST_CLIENT_SECRET=your-generated-local-test-secret
```

Replace the example secret with a random URL-safe secret of 32 to 256 characters.
Rebuild the backend with `docker compose up --build -d --wait backend`. The usual
Compose command starts the backend and its processing workers.

The client ID is `auth-demo-middleware`. When curl prompts for the password,
enter the value of `UPRAISE_JWT_TEST_CLIENT_SECRET` from `.env`.

```powershell
$jwt = (curl.exe -fsS -u auth-demo-middleware -d "grant_type=client_credentials" http://127.0.0.1:8000/oauth/token | ConvertFrom-Json).access_token
curl.exe -i -H "Authorization: Bearer $jwt" http://127.0.0.1:8000/v1/documents
```

On Ubuntu, use `curl` and Python 3 to read the JSON response.

```sh
jwt=$(curl -fsS -u auth-demo-middleware -d "grant_type=client_credentials" http://127.0.0.1:8000/oauth/token | python3 -c 'import json, sys; print(json.load(sys.stdin)["access_token"])')
curl -i -H "Authorization: Bearer $jwt" http://127.0.0.1:8000/v1/documents
```

Each JWT lasts one hour, and a backend restart invalidates existing test
tokens. Send only `grant_type=client_credentials` in the form. Regular APIs
use the authenticated client identity and course access.
The test client can access its own resources in `demo-course`. Materials from
JWT-free testing belong to the separate `local-test-client` identity.
Frontend2 requests its service JWT automatically in local JWT mode.

For production, use the deployment's OAuth provider and connect through the
configured mTLS gateway. [Ingress configuration](a1.md#ingress-configuration)

## Production/default authentication

Both code and committed Compose/example defaults are:

```dotenv
UPRAISE_ENVIRONMENT=production
UPRAISE_LOCAL_TEST_MODE=false
UPRAISE_LOCAL_JWT_TEST_MODE=false
```

Local mode requires both explicit development values. Enabling it while leaving
environment=production refuses application startup. Development alone does not
disable authentication; missing production identity/gateway settings still return
503 INGRESS_NOT_CONFIGURED. Production JWT signature/expiry/scope/course checks
and trusted mTLS-gateway enforcement are unchanged.

To return this workstation to production authentication, set these production
values in its ignored `.env`, configure actual OAuth/mTLS settings, and recreate
the backend and Frontend2. Local test documents keep their testing client identity; they
are not silently reassigned to a real Moodle integration. Do not deploy this
workstation's ignored development `.env` to a production environment.

Tests run with production defaults explicitly pinned, then exercise the real
local-mode branch using isolated storage and synthetic documents:

```sh
docker compose run --rm --no-deps --user root -e UPRAISE_ENVIRONMENT=production -e UPRAISE_LOCAL_TEST_MODE=false -v ./backend/src:/app/src:ro -v ./backend/tests:/tests:ro backend uv run --group dev pytest /tests -q -p no:cacheprovider
```
