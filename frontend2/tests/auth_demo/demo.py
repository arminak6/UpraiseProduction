"""Run a disposable browser → middleware → mTLS gateway → UPRAISE demo."""

import argparse
import hashlib
import hmac
import json
import os
import secrets
import time
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime
from http.cookies import CookieError, SimpleCookie
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Lock, Thread

import httpx
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from transport import (
    AUDIENCE,
    CLIENT_ID,
    COURSE,
    DemoServer,
    Gateway,
    Handler,
    TokenIssuer,
    basic_auth,
    certificates,
    client_tls,
    server_tls,
)

HERE = Path(__file__).resolve().parent
COOKIE = "upraise_auth_demo"
SESSION_TTL = 1200
ASSETS = {
    "/": (HERE / "index.html", "text/html; charset=utf-8"),
    "/demo.css": (HERE / "demo.css", "text/css; charset=utf-8"),
    "/demo.js": (HERE / "demo.js", "application/javascript; charset=utf-8"),
    "/styles.css": (Path("/ui/styles.css"), "text/css; charset=utf-8"),
}
CALLS = {"/demo/documents": "/v1/documents", "/demo/version": "/v1/version"}


def password_hash(password, salt):
    return hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)


class Accounts:
    def __init__(self, users, passwords):
        self.users = {}
        for username, user in users.items():
            salt = secrets.token_bytes(16)
            self.users[username] = (user, salt, password_hash(passwords[username], salt))
        salt = secrets.token_bytes(16)
        self.unknown_user = ({}, salt, password_hash(secrets.token_urlsafe(16), salt))
        self.sessions = {}
        self.lock = Lock()

    def login(self, username, password, previous):
        user, salt, expected = self.users.get(username, self.unknown_user)
        if not hmac.compare_digest(password_hash(password, salt), expected) or not user:
            return None
        token = secrets.token_urlsafe(32)
        now = time.monotonic()
        with self.lock:
            self.sessions = {key: value for key, value in self.sessions.items() if value[1] > now}
            self.sessions.pop(previous, None)
            self.sessions[token] = (user, now + SESSION_TTL)
        return token

    def get(self, token):
        with self.lock:
            session = self.sessions.get(token)
            if session and session[1] > time.monotonic():
                return session[0]
            self.sessions.pop(token, None)
        return None

    def logout(self, token):
        with self.lock:
            self.sessions.pop(token, None)


def issue_token(client, issuer, secret, scope):
    response = client.post(issuer + "/oauth/token", headers={"Authorization": basic_auth(secret)},
                           data={"grant_type": "client_credentials", "scope": scope})
    response.raise_for_status()
    return response.json()["access_token"]


class BrowserLogin:
    cookie_name = COOKIE
    cookie_path = "/demo"
    workspace_mode = False

    def session_cookie(self, value, age=SESSION_TTL):
        return f"{self.cookie_name}={value}; Path={self.cookie_path}; Max-Age={age}; HttpOnly; SameSite=Strict"

    def session_id(self):
        cookies = SimpleCookie()
        try:
            cookies.load(self.headers.get("Cookie", ""))
        except CookieError:
            return ""
        session = cookies.get(self.cookie_name)
        return session.value if session else ""

    def session_response(self, user, *, cookie=None):
        data = {"user": user["label"] if user else None}
        if self.workspace_mode:
            data["workspace"] = "/"
        self.json_response(200, data, cookie=cookie)

    def post_login(self):
        try:
            data = json.loads(Handler.read_body(self, "application/json") or b"")
        except ValueError:
            data = None
        if not isinstance(data, dict):
            self.json_response(400, {"detail": "INVALID_REQUEST"})
            return
        account = self.server.accounts
        current = self.session_id()
        if self.path == "/demo/logout":
            account.logout(current)
            self.session_response(None, cookie=self.session_cookie("", 0))
            return
        username, password = data.get("username"), data.get("password")
        if not all(isinstance(value, str) and 0 < len(value) <= 128 for value in (username, password)):
            self.json_response(400, {"detail": "INVALID_REQUEST"})
            return
        token = account.login(username, password, current)
        if token is None:
            self.json_response(401, {"detail": "INVALID_DEMO_CREDENTIALS"})
            return
        self.session_response(account.users[username][0], cookie=self.session_cookie(token))


class BrowserMiddleware(BrowserLogin, Handler):
    def trusted(self):
        port = self.server.server_port
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        host = self.headers.get("Host", "").lower()
        origin = self.headers.get("Origin")
        return (host in hosts and (not origin or origin == f"http://{host}")
                and self.headers.get("Sec-Fetch-Site") != "cross-site")

    def do_GET(self):
        if not self.trusted():
            self.json_response(403, {"detail": "UNTRUSTED_ORIGIN"})
            return
        if asset := ASSETS.get(self.path):
            self.respond(200, asset[0].read_bytes(), asset[1])
            return
        user = self.server.accounts.get(self.session_id())
        if self.path == "/demo/session":
            self.session_response(user)
        elif route := CALLS.get(self.path):
            if not user:
                self.json_response(401, {"detail": "DEMO_LOGIN_REQUIRED"})
                return
            try:
                token = issue_token(self.server.client, self.server.issuer, self.server.client_secret, user["scope"])
                result = self.server.client.get(self.server.gateway + route,
                                                headers={"Authorization": f"Bearer {token}"})
            except httpx.HTTPError:
                self.json_response(502, {"detail": "DEMO_SERVICE_UNAVAILABLE"})
                return
            self.json_response(result.status_code, {"endpoint": f"GET {route}", "body": result.json()})
        else:
            self.json_response(404, {"detail": "DEMO_ROUTE_NOT_FOUND"})

    def do_POST(self):
        if not self.trusted():
            self.json_response(403, {"detail": "UNTRUSTED_ORIGIN"})
            return
        if self.path not in {"/demo/login", "/demo/logout"}:
            self.json_response(404, {"detail": "DEMO_ROUTE_NOT_FOUND"})
            return
        self.post_login()


class SampleJobs:
    dsn = ""  # DocumentService constructs repositories lazily; this read uses only SampleJobs.

    def list_documents(self, client_id, course_id):
        if client_id != CLIENT_ID or course_id not in {None, COURSE}:
            return []
        now = datetime.now(UTC)
        return [{
            "document_id": "215615dd-7f88-46cb-8d81-0a75e47084da",
            "metadata": {"course_id": COURSE, "activity_id": "auth-demo", "language": "en",
                         "source_ref": "demo-1", "content_hash": "demo-1", "source_filename": "Sample lesson.pdf"},
            "status": "DONE", "progress": 1, "active_attempt": None, "created_at": now, "updated_at": now,
        }]


def backend_client(issuer, fingerprint, ca_file, *, workspace=False):
    os.environ.update({
        "SSL_CERT_FILE": str(ca_file),
        "UPRAISE_ENVIRONMENT": "production",
        "UPRAISE_LOCAL_TEST_MODE": "false",
        "UPRAISE_OAUTH_ISSUER": issuer,
        "UPRAISE_OAUTH_AUDIENCE": AUDIENCE,
        "UPRAISE_OAUTH_JWKS_URL": issuer + "/jwks.json",
        "UPRAISE_OAUTH_CLIENTS": json.dumps([CLIENT_ID]),
        "UPRAISE_TRUSTED_PROXY_CIDRS": '["127.0.0.1/32"]',
        "UPRAISE_MTLS_ALLOWED_FINGERPRINTS": json.dumps([fingerprint]),
    })
    from upraise_ai.core.config import get_settings
    from upraise_ai.main import app
    from upraise_ai.services.versions import VersionCatalog
    from upraise_ai.workflows.documents import DocumentService

    if not workspace:
        app.state.documents = DocumentService(SampleJobs(), None, None)
        app.state.versions = VersionCatalog(get_settings())
    # An in-process transport keeps the backend private to the certificate-verifying gateway.
    # The standalone demo installs read-only fixtures and does not start the normal app lifespan.
    return TestClient(app, client=("127.0.0.1", 443), raise_server_exceptions=False)


def serve(stack, server):
    stack.enter_context(server)
    thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
    thread.start()
    stack.callback(thread.join)
    stack.callback(server.shutdown)


@contextmanager
def demo(host, port, *, workspace=False):
    with ExitStack() as stack:
        directory = Path(stack.enter_context(TemporaryDirectory(prefix="upraise-auth-demo-")))
        fingerprint = certificates(directory)
        users = json.loads((HERE / "users.json").read_text(encoding="utf-8"))
        if workspace:
            users["teacher"]["scope"] = "documents:read documents:write jobs:read jobs:write lessons:write sessions:write"
            users["operator"]["scope"] = "documents:read jobs:read"
        passwords = {username: secrets.token_urlsafe(12) for username in users}
        secret = secrets.token_urlsafe(32)
        issuer = DemoServer(("127.0.0.1", 0), TokenIssuer, server_tls(directory))
        issuer.issuer = f"https://127.0.0.1:{issuer.server_port}"
        issuer.signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        issuer.client_secret = secret
        issuer.scopes = {scope for user in users.values() for scope in user["scope"].split()}
        serve(stack, issuer)

        backend = backend_client(issuer.issuer, fingerprint, directory / "ca.crt", workspace=workspace)
        if workspace:
            stack.enter_context(backend)
        else:
            stack.callback(backend.close)
        gateway = DemoServer(("127.0.0.1", 0), Gateway, server_tls(directory, mtls=True))
        gateway.backend = backend
        if workspace:
            from workspace import WORKSPACE_ROUTES

            gateway.routes = WORKSPACE_ROUTES
        serve(stack, gateway)

        gateway_url = f"https://127.0.0.1:{gateway.server_port}"
        if workspace:
            from workspace import workspace_server

            browser = workspace_server(host, port, gateway_url, directory)
        else:
            browser = DemoServer((host, port), BrowserMiddleware)
        browser.accounts = Accounts(users, passwords)
        browser.client = stack.enter_context(httpx.Client(verify=client_tls(directory, mtls=True), trust_env=False))
        browser.issuer = issuer.issuer
        browser.gateway = gateway_url
        browser.client_secret = secret
        serve(stack, browser)
        yield browser, issuer, directory, passwords


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", choices=["127.0.0.1", "0.0.0.0"], default="127.0.0.1")
    parser.add_argument("--port", type=int)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--workspace", action="store_true")
    args = parser.parse_args()
    port = (8502 if args.workspace else 8513) if args.port is None else args.port
    with demo(args.host, 0 if args.check else port, workspace=args.workspace) as (browser, issuer, directory, passwords):
        if args.check:
            if args.workspace:
                from check_demo import check_workspace

                check_workspace(browser, passwords)
            else:
                from check_demo import check

                check(browser, issuer, directory, passwords)
            return
        print(f"UPRAISE authentication demo: http://127.0.0.1:{browser.server_port}", flush=True)
        print("Demo sign-in credentials for this run:", flush=True)
        for username, password in passwords.items():
            print(f"  {username}: {password}", flush=True)
        try:
            Event().wait()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
