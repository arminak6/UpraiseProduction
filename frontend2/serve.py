"""Serve UPRAISE and a same-origin bridge to its existing HTTP backend."""

import argparse
import hmac
import json
import os
import re
import secrets
import socket
import ssl
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parent
ASSETS = {"/", "/index.html", "/styles.css", "/app.js", "/api.js", "/ui.js", "/voice.js", "/voice-capture-worklet.js", "/favicon.svg"}
UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
ROUTES = [
    ("GET", r"/health", set()),
    ("GET", r"/ready", set()),
    ("GET", r"/v1/documents", {"course_id"}),
    ("POST", r"/v1/documents", set()),
    ("DELETE", rf"/v1/documents/{UUID}", set()),
    ("GET", rf"/v1/documents/{UUID}/slides/[1-9][0-9]{{0,3}}", {"document_version_id"}),
    ("GET", r"/v1/lesson-jobs", {"course_id", "limit", "offset"}),
    ("GET", r"/v1/lesson-reviews", {"course_id", "limit", "offset"}),
    ("GET", rf"/v1/lesson-reviews/{UUID}", set()),
    ("POST", r"/v2/lesson-jobs", set()),
    ("GET", rf"/v1/jobs/{UUID}", set()),
    ("DELETE", rf"/v1/jobs/{UUID}", {"reason"}),
    ("GET", rf"/v1/jobs/{UUID}/result", {"learner_level"}),
    ("GET", rf"/v1/jobs/{UUID}/audio/{UUID}", set()),
    ("POST", r"/v1/audio:regenerate", set()),
    ("POST", r"/v1/sessions", set()),
    ("POST", rf"/v1/sessions/{UUID}/interrupts", set()),
    ("POST", rf"/v1/sessions/{UUID}/end", set()),
]
MAX_UPLOAD = 100 * 1024 * 1024 + 256 * 1024
MAX_JSON = 128 * 1024
MAX_VOICE_UPLOAD = 6_000_000 + 65536  # B4 audio bytes plus bounded multipart metadata.
MAX_RESPONSE = 128 * 1024 * 1024
DEFAULT_LEVELS = ["beginner", "intermediate", "advanced"]


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass
class Settings:
    backend_url: str = "http://127.0.0.1:8000"
    token: str = field(default="", repr=False)
    certificate: str = ""
    private_key: str = field(default="", repr=False)
    key_password: str = field(default="", repr=False)
    ca_certificate: str = ""
    public_port: int | None = None
    activity_id: str = "upraise-learning"
    levels: list[str] = field(default_factory=lambda: DEFAULT_LEVELS.copy())
    ui_nonce: str = field(default_factory=lambda: secrets.token_urlsafe(32), repr=False)

    def opener(self):
        parsed = urlsplit(self.backend_url)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment):
            raise ValueError("BACKEND_BASE_URL must be an HTTP(S) URL without credentials or query.")
        _ = parsed.port
        if bool(self.certificate) != bool(self.private_key):
            raise ValueError("Configure both FRONTEND2_CLIENT_CERT and FRONTEND2_CLIENT_KEY.")
        if self.certificate and parsed.scheme != "https":
            raise ValueError("Client certificates require an HTTPS backend gateway.")
        context = ssl.create_default_context(cafile=self.ca_certificate or None)
        if self.certificate:
            context.load_cert_chain(self.certificate, self.private_key, self.key_password or None)
        self.backend_url = self.backend_url.rstrip("/")
        return build_opener(ProxyHandler({}), NoRedirect(), HTTPSHandler(context=context))


class UpraiseServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, settings, handler=None):
        self.settings = settings
        self.upstream = settings.opener()
        super().__init__(address, handler or UpraiseHandler)


class UpraiseHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def setup(self):
        super().setup()
        self.connection.settimeout(30)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        super().end_headers()

    def log_message(self, format, *args):
        # Never log tokens, query parameters, uploaded filenames, or response bodies.
        pass

    def json_response(self, status, body, *, cookie=None):
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def trusted_request(self, *, nonce=False):
        port = self.server.settings.public_port or self.server.server_port
        allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if self.headers.get("Host", "").lower() not in allowed:
            self.json_response(HTTPStatus.FORBIDDEN, {"detail": "UNTRUSTED_HOST"})
            return False
        origin = self.headers.get("Origin")
        if origin and origin not in {f"http://{item}" for item in allowed}:
            self.json_response(HTTPStatus.FORBIDDEN, {"detail": "UNTRUSTED_ORIGIN"})
            return False
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            self.json_response(HTTPStatus.FORBIDDEN, {"detail": "UNTRUSTED_ORIGIN"})
            return False
        if nonce and not hmac.compare_digest(
            self.headers.get("X-UPRAISE-UI", "").encode("utf-8"), self.server.settings.ui_nonce.encode("ascii")
        ):
            self.json_response(HTTPStatus.FORBIDDEN, {"detail": "UI_SESSION_REQUIRED"})
            return False
        return True

    def do_GET(self):
        if not self.trusted_request():
            return
        path = urlsplit(self.path).path
        if path == "/api/config":
            self.json_response(200, self.configuration())
        elif path.startswith("/api/"):
            self.proxy()
        elif path in ASSETS:
            super().do_GET()
        else:
            self.json_response(404, {"detail": "NOT_FOUND"})

    def do_HEAD(self):
        if not self.trusted_request():
            return
        if urlsplit(self.path).path in ASSETS:
            super().do_HEAD()
        else:
            self.json_response(404, {"detail": "NOT_FOUND"})

    def do_POST(self):
        self.proxy()

    def do_DELETE(self):
        self.proxy()

    def configuration(self):
        settings = self.server.settings
        return {"backend_url": settings.backend_url, "ui_nonce": settings.ui_nonce,
                "activity_id": settings.activity_id, "levels": settings.levels}

    def bearer_token(self):
        return self.server.settings.token

    def proxy(self):
        if not self.trusted_request(nonce=True):
            return
        parsed = urlsplit(self.path)
        path = parsed.path.removeprefix("/api") if parsed.path.startswith("/api/") else ""
        route = next((entry for entry in ROUTES
                      if entry[0] == self.command and re.fullmatch(entry[1], path)), None)
        if (not route or parsed.fragment
                or any(key not in route[2] for key, _ in parse_qsl(parsed.query, keep_blank_values=True))):
            self.json_response(404, {"detail": "ENDPOINT_NOT_ALLOWED"})
            return
        body = None
        content_type = self.headers.get("Content-Type", "")
        if self.headers.get("Transfer-Encoding"):
            self.json_response(400, {"detail": "CONTENT_LENGTH_REQUIRED"})
            self.close_connection = True
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        media_type = content_type.split(";", 1)[0].strip().lower()
        is_upload = self.command == "POST" and path == "/v1/documents"
        is_voice = (self.command == "POST" and re.fullmatch(rf"/v1/sessions/{UUID}/interrupts", path)
                    and media_type == "multipart/form-data")
        limit = MAX_UPLOAD if is_upload else MAX_VOICE_UPLOAD if is_voice else MAX_JSON
        if length < 0 or length > limit:
            self.json_response(413, {"detail": "REQUEST_TOO_LARGE"})
            self.close_connection = True
            return
        if self.command == "POST":
            if not length:
                self.json_response(411, {"detail": "CONTENT_LENGTH_REQUIRED"})
                return
            expected_type = "multipart/form-data" if is_upload or is_voice else "application/json"
            if media_type != expected_type:
                self.json_response(415, {"detail": "UNSUPPORTED_CONTENT_TYPE"})
                self.close_connection = True
                return
        elif length:
            self.json_response(400, {"detail": "UNEXPECTED_REQUEST_BODY"})
            self.close_connection = True
            return
        if length:
            try:
                body = self.rfile.read(length)
            except (TimeoutError, OSError):
                self.close_connection = True
                return
            if len(body) != length:
                self.json_response(400, {"detail": "INCOMPLETE_REQUEST"})
                return
        headers = {"Accept": "application/json, audio/wav, image/png"}
        if content_type:
            headers["Content-Type"] = content_type
        for name in ("Idempotency-Key", "X-UPRAISE-Session-JWT"):
            value = self.headers.get(name)
            if value:
                headers[name] = value
        try:
            token = self.bearer_token().strip()
        except (URLError, OSError):
            self.json_response(502, {"detail": "BACKEND_UNAVAILABLE"})
            return
        if token:
            if token.lower().startswith("bearer "):
                token = token[7:].strip()
            headers["Authorization"] = f"Bearer {token}"
        target = self.server.settings.backend_url + path
        if parsed.query:
            target += "?" + parsed.query
        timeout = 600 if path.endswith("/interrupts") else 180 if self.command == "POST" else 30
        request = Request(target, data=body, headers=headers, method=self.command)
        try:
            try:
                upstream = self.server.upstream.open(request, timeout=timeout)
            except HTTPError as error:
                upstream = error
            with upstream:
                data = upstream.read(MAX_RESPONSE + 1)
                if len(data) > MAX_RESPONSE:
                    self.json_response(502, {"detail": "BACKEND_RESPONSE_TOO_LARGE"})
                    return
                if 300 <= upstream.code < 400:
                    self.json_response(502, {"detail": "BACKEND_REDIRECT_BLOCKED"})
                    return
                media_type = upstream.headers.get("Content-Type", "application/json")
                if upstream.code != 204 and media_type.split(";", 1)[0] not in {"application/json", "audio/wav", "image/png"}:
                    self.json_response(502, {"detail": "BACKEND_RESPONSE_INVALID"})
                    return
                self.send_response(upstream.code)
                self.send_header("Content-Type", media_type)
                self.send_header("Content-Length", str(len(data)))
                for name in ("Retry-After", "X-Request-ID", "X-UPRAISE-Auth-Mode"):
                    if name in upstream.headers:
                        self.send_header(name, upstream.headers[name])
                self.end_headers()
                self.wfile.write(data)
        except (socket.timeout, TimeoutError):
            self.json_response(504, {"detail": "BACKEND_TIMEOUT"})
        except (URLError, OSError):
            self.json_response(502, {"detail": "BACKEND_UNAVAILABLE"})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8502)
    parser.add_argument("--host", default=os.environ.get("FRONTEND2_HOST", "127.0.0.1"),
                        choices=["127.0.0.1", "0.0.0.0"])
    parser.add_argument("--backend", default=os.environ.get("BACKEND_BASE_URL", "http://127.0.0.1:8000"))
    args = parser.parse_args()
    try:
        profiles = json.loads(os.environ.get("UPRAISE_LESSON_LEVEL_PROFILES") or "{}")
    except json.JSONDecodeError:
        parser.error("UPRAISE_LESSON_LEVEL_PROFILES must be a JSON object.")
    if not isinstance(profiles, dict):
        parser.error("UPRAISE_LESSON_LEVEL_PROFILES must be a JSON object.")
    levels = list(profiles) or DEFAULT_LEVELS.copy()
    if not 1 <= len(levels) <= 8 or not all(re.fullmatch(r"[a-z][a-z0-9_]{0,39}", name) for name in levels):
        parser.error("UPRAISE_LESSON_LEVEL_PROFILES must contain one to eight valid level names.")
    settings = Settings(
        backend_url=args.backend,
        token=os.environ.get("FRONTEND2_BACKEND_TOKEN", ""),
        certificate=os.environ.get("FRONTEND2_CLIENT_CERT", ""),
        private_key=os.environ.get("FRONTEND2_CLIENT_KEY", ""),
        key_password=os.environ.get("FRONTEND2_KEY_PASSWORD", ""),
        ca_certificate=os.environ.get("FRONTEND2_CA_CERT", ""),
        public_port=int(os.environ["FRONTEND2_PUBLIC_PORT"]) if os.environ.get("FRONTEND2_PUBLIC_PORT") else None,
        activity_id=os.environ.get("FRONTEND2_ACTIVITY_ID", "upraise-learning"), levels=levels,
    )
    with UpraiseServer((args.host, args.port), settings) as server:
        print(f"UPRAISE: http://127.0.0.1:{settings.public_port or server.server_port}", flush=True)
        print("Connected workspace. Press Ctrl+C to stop.", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
