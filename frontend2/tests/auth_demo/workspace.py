"""Add demo sign-in to the existing Frontend2 bridge."""

from urllib.error import URLError
from urllib.parse import urlsplit

import httpx
from demo import ASSETS, BrowserLogin, issue_token
from serve import ROUTES, Settings, UpraiseHandler, UpraiseServer
from transport import COURSE

WORKSPACE_ROUTES = ROUTES


class WorkspaceHandler(BrowserLogin, UpraiseHandler):
    cookie_name = "upraise_auth_workspace"
    cookie_path = "/"
    workspace_mode = True

    def user(self):
        return self.server.accounts.get(self.session_id())

    def redirect(self, target):
        self.send_response(303)
        self.send_header("Location", target)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        if not self.trusted_request():
            return
        path = urlsplit(self.path).path
        if path == "/demo/session":
            self.session_response(self.user())
        elif path in {"/login", "/demo.css", "/demo.js"}:
            if path == "/login" and self.user():
                self.redirect("/")
                return
            asset, content_type = ASSETS["/" if path == "/login" else path]
            content = asset.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'; form-action 'self'")
            self.end_headers()
            self.wfile.write(content)
        elif path in {"/", "/index.html"} and not self.user():
            self.redirect("/login")
        elif path == "/api/config" and not (user := self.user()):
            self.json_response(401, {"detail": "DEMO_LOGIN_REQUIRED"})
        elif path == "/api/config":
            self.json_response(200, {**self.configuration(), "demo_user": user["label"], "demo_course": COURSE})
        else:
            super().do_GET()

    def do_HEAD(self):
        if not self.trusted_request():
            return
        if urlsplit(self.path).path in {"/", "/index.html"} and not self.user():
            self.redirect("/login")
        else:
            super().do_HEAD()

    def do_POST(self):
        if self.path in {"/demo/login", "/demo/logout"}:
            if self.trusted_request():
                self.post_login()
        else:
            super().do_POST()

    def proxy(self):
        if not self.trusted_request():
            return
        account = self.user()
        if not account:
            self.json_response(401, {"detail": "DEMO_LOGIN_REQUIRED"})
            return
        if account["read_only"] and self.command not in {"GET", "HEAD"}:
            self.json_response(403, {"detail": "DEMO_ACCESS_DENIED"})
            return
        super().proxy()

    def bearer_token(self):
        try:
            return issue_token(self.server.client, self.server.issuer, self.server.client_secret)
        except httpx.HTTPError as error:
            raise URLError("Demo token issuer unavailable") from error


def workspace_server(host, port, gateway, directory):
    settings = Settings(backend_url=gateway, certificate=str(directory / "middleware.crt"),
                        private_key=str(directory / "middleware.key"), ca_certificate=str(directory / "ca.crt"))
    return UpraiseServer((host, port), settings, WorkspaceHandler)
