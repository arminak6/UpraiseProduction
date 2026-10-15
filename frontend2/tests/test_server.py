"""Exercise the real local bridge against a disposable HTTP backend."""

import hashlib
import http.client
import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from serve import Settings, UpraiseServer  # noqa: E402

JOB = "10000000-0000-4000-8000-000000000001"
CHUNK = "20000000-0000-4000-8000-000000000001"
WAV = b"RIFF\x04\x00\x00\x00WAVEtest"


class FakeBackend(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def handle_request(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b""
        self.server.calls.append((self.command, self.path, dict(self.headers), body))
        status = self.server.status
        headers = self.server.response_headers.copy()
        data = self.server.data
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = handle_request
    do_POST = handle_request
    do_DELETE = handle_request


class BridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.backend = ThreadingHTTPServer(("127.0.0.1", 0), FakeBackend)
        cls.thread = threading.Thread(target=cls.backend.serve_forever, daemon=True)
        cls.thread.start()
        cls.settings = Settings(backend_url=f"http://127.0.0.1:{cls.backend.server_port}", token="test-service-token")
        cls.frontend = UpraiseServer(("127.0.0.1", 0), cls.settings)
        cls.frontend_thread = threading.Thread(target=cls.frontend.serve_forever, daemon=True)
        cls.frontend_thread.start()

    @classmethod
    def tearDownClass(cls):
        for server in (cls.frontend, cls.backend):
            server.shutdown()
            server.server_close()
        cls.frontend_thread.join(timeout=5)
        cls.thread.join(timeout=5)

    def setUp(self):
        self.backend.calls = []
        self.backend.status = 200
        self.backend.response_headers = {"Content-Type": "application/json", "X-UPRAISE-Auth-Mode": "local-test"}
        self.backend.data = b"[]"

    def request(self, path, *, method="GET", body=None, headers=None, nonce=True):
        values = {"X-UPRAISE-UI": self.settings.ui_nonce} if nonce else {}
        values.update(headers or {})
        connection = http.client.HTTPConnection("127.0.0.1", self.frontend.server_port, timeout=5)
        try:
            connection.request(method, path, body=body, headers=values)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_static_assets_and_hidden_project_files(self):
        for path in ("/", "/index.html", "/app.js", "/api.js", "/ui.js", "/styles.css", "/favicon.svg"):
            with self.subTest(path=path):
                status, headers, body = self.request(path, nonce=False)
                self.assertEqual(status, 200)
                self.assertTrue(body)
                self.assertEqual(headers["Cache-Control"], "no-store")
        for path in ("/serve.py", "/README.md", "/../.env", "/demo-data.js", "/tests/test_server.py"):
            self.assertEqual(self.request(path)[0], 404)
        self.assertFalse(self.backend.calls)

    def test_config_does_not_disclose_service_credentials(self):
        status, _, body = self.request("/api/config", nonce=False)
        self.assertEqual(status, 200)
        config = json.loads(body)
        self.assertEqual(config["ui_nonce"], self.settings.ui_nonce)
        self.assertEqual(config["levels"], ["beginner", "intermediate", "advanced"])
        self.assertNotIn(b"test-service-token", body)

    def test_nonce_host_and_origin_checks(self):
        self.assertEqual(self.request("/api/v1/documents", nonce=False)[0], 403)
        self.assertEqual(self.request("/api/v1/documents", headers={"X-UPRAISE-UI": "invalid-é"})[0], 403)
        for headers in ({"Host": "evil.example"}, {"Origin": "https://evil.example"}, {"Sec-Fetch-Site": "cross-site"}):
            self.assertEqual(self.request("/api/config", headers=headers)[0], 403)
        self.assertFalse(self.backend.calls)

    def test_course_catalog_forwarding_and_credential_allowlist(self):
        self.request("/api/v1/lesson-jobs?limit=100&offset=100", headers={
            "Authorization": "Bearer browser-token", "X-SSL-Client-Verify": "SUCCESS",
            "Cookie": "do-not-forward=1", "X-Request-ID": "not-trusted",
        })
        method, path, headers, _ = self.backend.calls[-1]
        self.assertEqual((method, path), ("GET", "/v1/lesson-jobs?limit=100&offset=100"))
        lower = {key.lower(): value for key, value in headers.items()}
        self.assertEqual(lower["authorization"], "Bearer test-service-token")
        for key in ("cookie", "x-ssl-client-verify", "x-upraise-ui", "x-request-id"):
            self.assertNotIn(key, lower)

    def test_json_and_session_assertion_forwarding(self):
        body = json.dumps({"lesson_job_id": JOB}).encode()
        self.request("/api/v1/sessions", method="POST", body=body, headers={
            "Content-Type": "application/json", "X-UPRAISE-Session-JWT": "signed-session-assertion",
        })
        _, _, headers, received = self.backend.calls[-1]
        lower = {key.lower(): value for key, value in headers.items()}
        self.assertEqual(lower["x-upraise-session-jwt"], "signed-session-assertion")
        self.assertEqual(received, body)

    def test_multipart_upload_and_retry_key_preserved(self):
        body = b"--boundary\r\nContent-Disposition: form-data; name=\"file\"; filename=\"source.csv\"\r\nContent-Type: text/csv\r\n\r\nname,value\na,1\r\n--boundary--\r\n"
        self.request("/api/v1/documents", method="POST", body=body, headers={
            "Content-Type": "multipart/form-data; boundary=boundary", "Idempotency-Key": "upload-retry-123",
        })
        _, _, headers, received = self.backend.calls[-1]
        self.assertEqual(received, body)
        self.assertEqual({key.lower(): value for key, value in headers.items()}["idempotency-key"], "upload-retry-123")

    def test_arbitrary_routes_and_query_parameters_are_blocked(self):
        for path in ("/api/v1/operations", "/api/v1/documents?url=https://evil.example", "/api/https://evil.example", "/api/v1/jobs/not-a-uuid", "/api/v1/jobs/%2e%2e", "/api/v1/version"):
            self.assertEqual(self.request(path)[0], 404, path)
        self.assertFalse(self.backend.calls)

    def test_limits_and_content_types(self):
        self.assertEqual(self.request("/api/v2/lesson-jobs", method="POST", body=b"{}", headers={"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.request("/api/v2/lesson-jobs", method="POST", body=b"{}", headers={"Content-Type": "application/json", "Content-Length": "999999999"})[0], 413)
        self.assertEqual(self.request("/api/v2/lesson-jobs", method="POST", body=b"")[0], 411)
        self.assertFalse(self.backend.calls)

    def test_audio_bytes_are_not_changed(self):
        self.backend.data = WAV
        self.backend.response_headers["Content-Type"] = "audio/wav"
        status, headers, body = self.request(f"/api/v1/jobs/{JOB}/audio/{CHUNK}")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "audio/wav")
        self.assertEqual(hashlib.sha256(body).digest(), hashlib.sha256(WAV).digest())

    def test_backend_errors_and_safe_headers_are_preserved(self):
        self.backend.status = 503
        self.backend.data = b'{"detail":"CLEANUP_PENDING"}'
        self.backend.response_headers.update({"Retry-After": "2", "Set-Cookie": "no=1", "Authorization": "secret"})
        status, headers, body = self.request(f"/api/v1/documents/{JOB}", method="DELETE")
        self.assertEqual(status, 503)
        self.assertEqual(json.loads(body)["detail"], "CLEANUP_PENDING")
        self.assertEqual(headers["Retry-After"], "2")
        self.assertNotIn("Set-Cookie", headers)
        self.assertNotIn("Authorization", headers)

    def test_redirects_never_follow_credentials_to_another_origin(self):
        self.backend.status = 302
        self.backend.response_headers["Location"] = "http://127.0.0.1:1/stolen"
        status, _, body = self.request("/api/v1/documents")
        self.assertEqual(status, 502)
        self.assertEqual(json.loads(body)["detail"], "BACKEND_REDIRECT_BLOCKED")
        self.assertEqual(len(self.backend.calls), 1)

    def test_gateway_html_is_not_reflected_into_the_ui(self):
        self.backend.status = 500
        self.backend.response_headers["Content-Type"] = "text/html"
        self.backend.data = b"<html>private gateway diagnostics</html>"
        status, _, body = self.request("/api/v1/documents")
        self.assertEqual(status, 502)
        self.assertNotIn(b"private gateway", body)

    def test_invalid_gateway_settings_fail_without_http_calls(self):
        for value in ("file:///etc/passwd", "http://token@localhost", "https://localhost?token=secret"):
            with self.assertRaises(ValueError):
                Settings(backend_url=value).opener()
        with self.assertRaises(ValueError):
            Settings(certificate="client.pem").opener()

    def test_session_close_and_cancellation_keep_request_semantics(self):
        self.backend.status = 204
        self.backend.data = b""
        self.assertEqual(self.request(f"/api/v1/sessions/{JOB}/end", method="POST", body=b'{"reason":"COMPLETED"}', headers={"Content-Type": "application/json"})[0], 204)
        self.assertEqual(self.request(f"/api/v1/jobs/{JOB}?reason=CANCEL", method="DELETE")[0], 204)
        self.assertEqual(self.backend.calls[-1][1], f"/v1/jobs/{JOB}?reason=CANCEL")


if __name__ == "__main__":
    unittest.main()
