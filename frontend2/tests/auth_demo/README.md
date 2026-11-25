# UPRAISE authentication demo

The browser signs in to a test middleware with a username and password. The
middleware requests a JWT from a local HTTPS token issuer using OAuth client
credentials, then calls a local gateway with that JWT and a client certificate.
The gateway verifies the client certificate and sends the request to the real
UPRAISE FastAPI application. The application runs with `production` and
`local_test_mode=false` and fetches the issuer’s public signing key over HTTPS.

## Sign in to the main UI on port 8502

From the repository root, with Docker Desktop running:

```powershell
docker compose -f frontend2/tests/auth_demo/workspace.compose.yaml up workspace
```

If the usual Frontend2 container is already using port 8502, stop that container
before starting the authentication workspace. Both use the same UI address.
If the backend image is missing, run `docker compose build backend` first.

Open [http://127.0.0.1:8502](http://127.0.0.1:8502). The terminal prints the
temporary `teacher` and `operator` passwords. Sign in to open Upload material,
Generate draft, Teacher review and Preview courses. The Teacher can submit
authoring requests for `demo-course`. The Operator can read the workspace but
receives `403` when submitting an upload. **Sign out** returns to the login page.

1. The browser sends the demo username and password to the UI server and
   receives an HTTP-only session cookie when sign-in succeeds.
2. When the browser loads documents, it calls `GET /api/v1/documents` on
   the UI server with that cookie.
3. The UI server checks the session and sends `POST /oauth/token` to the local
   issuer using its own client ID, client secret, and `grant_type=client_credentials`.
4. The UI server calls `GET /v1/documents` through the gateway with
   `Authorization: Bearer <JWT>`. The backend verifies the JWT and returns
   the documents. The service credentials and JWT stay on the server.

This Compose project starts a clean PostgreSQL and Qdrant workspace with the real
backend routes. This authentication setup starts no processing workers. Uploads
and generation requests can be tested for authorization and acceptance. Jobs
stay queued. Lesson playback retains the backend’s signed session assertion requirement.

Run the integrated checks while the workspace databases are running:

```powershell
docker compose -f frontend2/tests/auth_demo/workspace.compose.yaml run --rm --no-deps workspace python demo.py --workspace --check
```

## Standalone authentication tester on port 8513

This version exposes the real `GET /v1/documents` and `GET /v1/version` handlers.
It uses one sample document and an in-process connection from the gateway to
FastAPI. It needs no project credentials, database, or AWS access. It runs as
a separate Docker Compose project and publishes only the browser UI to loopback.

From the repository root, with Docker Desktop running:

```powershell
docker compose -f frontend2/tests/auth_demo/compose.yaml up
```

The demo uses the existing `upraise-backend:latest` image. If Docker says the
image is missing, build it from the root with `docker compose build backend`.

1. Open [http://127.0.0.1:8513](http://127.0.0.1:8513).
2. Sign in as `teacher` with the password printed in the terminal. **List
   documents** returns `200` and **Read backend version** returns `403`.
3. Sign out and sign in as `operator` using its terminal password. The version
   call returns `200` and the documents call returns `403`.
4. Sign out and try a wrong password to see the login error.

The test accounts and their middleware permissions are in [users.json](users.json). Each run generates
fresh passwords, stores salted password hashes in memory, and creates temporary
certificates and signing keys. The browser receives only an HTTP-only demo
session cookie. Stop the foreground process with Ctrl+C.

## Test JWTs with curl on port 8514

For the full Compose backend, [request a test JWT on port 8000](../../../backend/docs/local-testing.md#direct-curl-with-a-jwt).

This demo uses one sample document. It sends the JWT through the demo's mTLS
gateway to the real UPRAISE document route. It runs alongside the main
Compose stack and generates fresh test credentials on every start.

Run this from the root of a fresh clone. Skip the build if you already built
the backend image.

```sh
docker compose build backend
docker compose -f frontend2/tests/auth_demo/compose.yaml run --rm --publish 127.0.0.1:8514:8514 auth-demo python curl_demo.py --host 0.0.0.0
```

Keep that terminal open and use the test client secret it prints when curl
prompts for the password. In a second terminal on the same machine, get the
JWT and call the documents API. The demo is reachable on the Docker host's
loopback address. For an Ubuntu server, run curl on the server or use SSH
port forwarding to reach it from your own computer.

```powershell
$jwt = (curl.exe -fsS -u auth-demo-middleware -d "grant_type=client_credentials" http://127.0.0.1:8514/oauth/token | ConvertFrom-Json).access_token
curl.exe -i -H "Authorization: Bearer $jwt" http://127.0.0.1:8514/v1/documents
```

On Ubuntu, use `curl` and Python 3 to read the JSON response.

```sh
jwt=$(curl -fsS -u auth-demo-middleware -d "grant_type=client_credentials" http://127.0.0.1:8514/oauth/token | python3 -c 'import json, sys; print(json.load(sys.stdin)["access_token"])')
curl -i -H "Authorization: Bearer $jwt" http://127.0.0.1:8514/v1/documents
```

A valid JWT returns `200` with the sample document. An omitted or invalid JWT
returns `401`. Each JWT lasts five minutes. Press Ctrl+C in the first terminal
to stop the demo.

## Run the automated check

```powershell
docker compose -f frontend2/tests/auth_demo/compose.yaml run --rm auth-demo python demo.py --check
```

The check covers successful and failed login, sign-out, OAuth client credentials,
the actual TLS handshake, both users’ permissions, rejected tokens, and course
access. It uses temporary ports and prints the results without showing credentials.
