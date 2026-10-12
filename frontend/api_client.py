"""HTTP-only client for the development test interface."""

import ssl
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

import httpx


@dataclass
class Connection:
    base_url: str
    token: str = field(default="", repr=False)
    certificate: bytes | None = field(default=None, repr=False)
    private_key: bytes | None = field(default=None, repr=False)
    key_password: str = field(default="", repr=False)
    ca_certificate: bytes | None = field(default=None, repr=False)
    timeout_seconds: float = 180


@dataclass
class Result:
    method: str
    path: str
    status: int | None
    elapsed_ms: int
    body: object = None
    is_json: bool = False
    headers: dict = field(default_factory=dict)
    error: str | None = None
    binary: bytes | None = field(default=None, repr=False)

    @property
    def successful(self):
        return self.status is not None and 200 <= self.status < 300


def normalize_base_url(value: str) -> str:
    value = value.strip().rstrip("/")
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError:
        raise ValueError("Enter a valid backend URL.") from None
    if (
        parsed.scheme not in ("http", "https") or not parsed.hostname
        or parsed.username is not None or parsed.password is not None
        or parsed.query or parsed.fragment
    ):
        raise ValueError("Use an HTTP or HTTPS backend URL without credentials, query or fragment.")
    return value


def status_path(value: str, base_url: str) -> str:
    """Never send credentials to another origin supplied in a Location header."""
    value = value.strip()
    if not value:
        raise ValueError("Enter a status URL or select a submitted document.")
    if "://" in value:
        try:
            target, base = httpx.URL(value), httpx.URL(normalize_base_url(base_url))
        except httpx.InvalidURL:
            raise ValueError("Use a valid job-status URL.") from None
        if (target.scheme, target.host, target.port) != (base.scheme, base.host, base.port):
            raise ValueError("The status URL must use the configured backend origin.")
        if target.username or target.password or target.query or target.fragment:
            raise ValueError("Use a plain job-status URL.")
        value = target.path
        prefix = base.path.rstrip("/")
        if prefix and value.startswith(prefix + "/"):
            value = value[len(prefix):]
    if value.startswith("/v1/jobs/"):
        value = value.removeprefix("/v1/jobs/")
    try:
        return "/v1/jobs/" + str(UUID(value))
    except ValueError:
        raise ValueError("Use /v1/jobs/<UUID> or a job UUID.") from None


def _tls_context(connection: Connection):
    if bool(connection.certificate) != bool(connection.private_key):
        raise ValueError("Provide both the client certificate and its private key.")
    if connection.certificate and not connection.base_url.startswith("https://"):
        raise ValueError("Client certificates require an HTTPS gateway URL.")
    context = ssl.create_default_context()
    if connection.ca_certificate:
        context.load_verify_locations(cadata=connection.ca_certificate.decode("utf-8"))
    if connection.certificate:
        # SSLContext requires filenames; delete the private directory before HTTP.
        with tempfile.TemporaryDirectory(prefix="upraise-test-tls-") as folder:
            cert, key = Path(folder) / "client.pem", Path(folder) / "key.pem"
            cert.write_bytes(connection.certificate)
            key.write_bytes(connection.private_key)
            cert.chmod(0o600)
            key.chmod(0o600)
            context.load_cert_chain(cert, key, password=connection.key_password or None)
    return context


def request(connection: Connection, method: str, path: str, *, params=None, json_body=None,
            fields=None, upload=None, upload_field="file", headers=None, timeout=None, transport=None) -> Result:
    started = time.monotonic()
    result = Result(method.upper(), path, None, 0)
    try:
        base = normalize_base_url(connection.base_url)
        if not path.startswith("/") or path.startswith("//") or "?" in path or "#" in path:
            raise ValueError("Enter an endpoint path starting with /; use query parameters separately.")
        request_headers = httpx.Headers(headers or {})
        token = connection.token.strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        if token:
            request_headers["Authorization"] = f"Bearer {token}"
        context = _tls_context(connection)
        with httpx.Client(
            verify=context, follow_redirects=False, trust_env=False, transport=transport,
            timeout=httpx.Timeout(timeout or connection.timeout_seconds, connect=10, pool=5),
        ) as client:
            response = client.request(
                result.method, base + path, params=params, headers=request_headers,
                json=json_body, data=fields, files={upload_field: upload} if upload else None,
            )
        result.status = response.status_code
        result.headers = {
            name: response.headers[name]
            for name in ("location", "content-type", "retry-after", "x-request-id", "x-upraise-auth-mode")
            if name in response.headers
        }
        if response.headers.get("content-type", "").split(";")[0] == "audio/wav":
            result.binary = response.content
            result.body = f"WAV audio: {len(response.content)} bytes"
        else:
            try:
                result.body = response.json()
                result.is_json = True
            except ValueError:
                result.body = response.text
    except httpx.TimeoutException:
        if result.method == "DELETE" and path.startswith("/v1/jobs/"):
            result.error = "Request timed out. The job action may have completed; refresh status or retry the same job ID and reason."
        elif result.method == "DELETE":
            result.error = "Request timed out. Deletion may have completed; refresh the list or retry the same document ID."
        elif result.method == "POST" and path in {"/v1/documents", "/v1/lesson-jobs", "/v2/lesson-jobs"}:
            result.error = "Request timed out. The submission may already be accepted; retry with the same Idempotency-Key."
        elif result.method == "POST" and path == "/v1/sessions":
            result.error = "Request timed out. The session may already be open; use Retry session start to resend the same session request."
        elif result.method == "POST" and path.endswith("/interrupts"):
            result.error = "Request timed out. Use Retry interruption with the same request ID; do not submit a new ID while it is running."
        elif result.method == "POST" and path.endswith("/end"):
            result.error = "Request timed out. The session may already be ended; retry the same session ID and reason."
        else:
            result.error = "Request timed out. Check the backend and retry."
    except httpx.RequestError:
        result.error = "Could not connect to the backend. Check its address, gateway and TLS configuration."
    except (ssl.SSLError, UnicodeError, OSError):
        result.error = "TLS configuration failed. Check the PEM certificate, private key, password and CA."
    except ValueError as error:
        result.error = str(error)
    result.elapsed_ms = round((time.monotonic() - started) * 1000)
    return result
