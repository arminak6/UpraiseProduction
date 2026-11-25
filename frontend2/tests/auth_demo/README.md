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

The usernames and scopes are in [users.json](users.json). Each run generates
fresh passwords, stores salted password hashes in memory, and creates temporary
certificates and signing keys. The browser receives only an HTTP-only demo
session cookie. Stop the foreground process with Ctrl+C.

## Run the automated check

```powershell
docker compose -f frontend2/tests/auth_demo/compose.yaml run --rm auth-demo python demo.py --check
```

The check covers successful and failed login, sign-out, OAuth client credentials,
the actual TLS handshake, both users’ permissions, rejected tokens, and course
access. It uses temporary ports and prints the results without showing credentials.
