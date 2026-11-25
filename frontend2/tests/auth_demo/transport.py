"""Temporary certificates, an HTTPS token issuer and a local mTLS gateway."""

import base64
import hashlib
import hmac
import json
import re
import ssl
import sys
import time
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import ip_address
from urllib.parse import parse_qs, parse_qsl, urlsplit

import jwt
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

CLIENT_ID = "auth-demo-middleware"
AUDIENCE = "upraise-auth-demo"
KEY_ID = "demo-key"
COURSE = "demo-course"
TOKEN_TTL = 300
READ_ROUTES = [("GET", "/v1/documents", {"course_id"}), ("GET", "/v1/version", set())]


def certificates(directory):
    now = datetime.now(UTC)

    def issue(name, purpose=None, signer=None):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
        is_ca = signer is None
        issuer, signing_key = (subject, key) if is_ca else (signer[0].subject, signer[1])
        builder = (x509.CertificateBuilder().subject_name(subject).issuer_name(issuer)
                   .public_key(key.public_key()).serial_number(x509.random_serial_number())
                   .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=1))
                   .add_extension(x509.BasicConstraints(ca=is_ca, path_length=0 if is_ca else None), True)
                   .add_extension(x509.KeyUsage(
                       digital_signature=not is_ca, content_commitment=False, key_encipherment=False,
                       data_encipherment=False, key_agreement=False, key_cert_sign=is_ca,
                       crl_sign=is_ca, encipher_only=False, decipher_only=False,
                   ), True)
                   .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), False)
                   .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(signing_key.public_key()), False))
        if purpose:
            builder = builder.add_extension(x509.ExtendedKeyUsage([purpose]), False)
        if purpose == ExtendedKeyUsageOID.SERVER_AUTH:
            builder = builder.add_extension(x509.SubjectAlternativeName([
                x509.DNSName("localhost"), x509.IPAddress(ip_address("127.0.0.1")),
            ]), False)
        cert = builder.sign(signing_key, hashes.SHA256())
        (directory / f"{name}.crt").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        if not is_ca:
            (directory / f"{name}.key").write_bytes(key.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
            ))
        return cert, key

    ca = issue("ca")
    issue("server", ExtendedKeyUsageOID.SERVER_AUTH, ca)
    client_cert, _ = issue("middleware", ExtendedKeyUsageOID.CLIENT_AUTH, ca)
    return client_cert.fingerprint(hashes.SHA256()).hex()


def server_tls(directory, *, mtls=False):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(directory / "server.crt", directory / "server.key")
    if mtls:
        context.load_verify_locations(cafile=str(directory / "ca.crt"))
        context.verify_mode = ssl.CERT_REQUIRED
    return context


def client_tls(directory, *, mtls=False):
    context = ssl.create_default_context(cafile=str(directory / "ca.crt"))
    if mtls:
        context.load_cert_chain(directory / "middleware.crt", directory / "middleware.key")
    return context


class DemoServer(ThreadingHTTPServer):
    def __init__(self, address, handler, context=None):
        self.context = context
        super().__init__(address, handler)

    def get_request(self):
        connection, peer = super().get_request()
        connection.settimeout(5)
        if self.context:
            connection = self.context.wrap_socket(connection, server_side=True, do_handshake_on_connect=False)
        return connection, peer

    def handle_error(self, request, client_address):
        # An unsuccessful TLS handshake is an expected result in the demo.
        if not isinstance(sys.exc_info()[1], OSError):
            super().handle_error(request, client_address)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def respond(self, status, data=b"", kind="application/json", *, cookie=None, headers=None):
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'; form-action 'self'")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def json_response(self, status, body, *, cookie=None):
        self.respond(status, json.dumps(body).encode(), cookie=cookie)

    def read_body(self, kind):
        if self.headers.get_content_type() != kind or self.headers.get("Transfer-Encoding"):
            return None
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return None
        return self.rfile.read(length) if 0 < length <= 4096 else None


def sign_access_token(key, issuer, *, lifetime=TOKEN_TTL):
    return jwt.encode({
        "iss": issuer, "aud": AUDIENCE, "client_id": CLIENT_ID,
        "exp": int(time.time()) + lifetime, "upraise_courses": [COURSE],
    }, key, algorithm="RS256", headers={"kid": KEY_ID})


def basic_auth(secret):
    return "Basic " + base64.b64encode(f"{CLIENT_ID}:{secret}".encode()).decode()


class TokenIssuer(Handler):
    def do_GET(self):
        if self.path == "/jwks.json":
            jwk = jwt.algorithms.RSAAlgorithm.to_jwk(self.server.signing_key.public_key(), as_dict=True)
            self.json_response(200, {"keys": [{**jwk, "kid": KEY_ID, "use": "sig", "alg": "RS256"}]})
        else:
            self.json_response(404, {"error": "not_found"})

    def do_POST(self):
        if self.path != "/oauth/token":
            self.json_response(404, {"error": "not_found"})
            return
        provided = self.headers.get("Authorization", "").encode()
        if not hmac.compare_digest(provided, basic_auth(self.server.client_secret).encode()):
            self.json_response(401, {"error": "invalid_client"})
            return
        body = self.read_body("application/x-www-form-urlencoded")
        form = parse_qs(body.decode("ascii", errors="replace"), keep_blank_values=True) if body else {}
        if form.get("grant_type") != ["client_credentials"]:
            self.json_response(400, {"error": "unsupported_grant_type"})
            return
        if "scope" in form:
            self.json_response(400, {"error": "invalid_scope"})
            return
        token = sign_access_token(self.server.signing_key, self.server.issuer)
        self.json_response(200, {"access_token": token, "token_type": "Bearer", "expires_in": TOKEN_TTL})


class Gateway(Handler):
    def forward(self):
        path = urlsplit(self.path)
        route = next((entry for entry in getattr(self.server, "routes", READ_ROUTES)
                      if self.command == entry[0] and re.fullmatch(entry[1], path.path)), None)
        if (not route or path.netloc or path.scheme or path.fragment
                or any(key not in route[2] for key, _ in parse_qsl(path.query, keep_blank_values=True))):
            self.json_response(404, {"detail": "DEMO_ROUTE_NOT_FOUND"})
            return
        body = None
        if self.command == "POST":
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if not 0 < length <= 101 * 1024 * 1024 or self.headers.get("Transfer-Encoding"):
                self.json_response(400, {"detail": "INVALID_CONTENT_LENGTH"})
                return
            body = self.rfile.read(length)
            if len(body) != length:
                self.json_response(400, {"detail": "INCOMPLETE_REQUEST"})
                return
        certificate = self.connection.getpeercert(binary_form=True)
        headers = {
            "X-SSL-Client-Verify": "SUCCESS",
            "X-SSL-Client-Fingerprint": hashlib.sha256(certificate).hexdigest(),
        }
        for name in ("Authorization", "Content-Type", "Idempotency-Key", "X-UPRAISE-Session-JWT"):
            if value := self.headers.get(name):
                headers[name] = value
        result = self.server.backend.request(self.command, self.path, headers=headers, content=body,
                                             follow_redirects=False)
        forwarded = {name: result.headers[name] for name in ("Retry-After", "X-Request-ID", "WWW-Authenticate")
                     if name in result.headers}
        self.respond(result.status_code, result.content, result.headers.get("Content-Type", "application/json"),
                     headers=forwarded)

    do_GET = forward
    do_POST = forward
    do_DELETE = forward
