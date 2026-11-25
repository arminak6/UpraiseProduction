"""Start the normal backend with a private issuer for local curl testing."""

import json
import logging
import os
import secrets
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread

import httpx
import uvicorn
from cryptography.hazmat.primitives.asymmetric import rsa
from demo import issue_token, serve
from transport import (
    AUDIENCE,
    CLIENT_ID,
    TOKEN_TTL,
    DemoServer,
    TokenIssuer,
    certificates,
    client_tls,
    server_tls,
)

ACCESS_TOKEN = Path("/tmp/upraise-access-token")
SCOPES = "documents:read documents:write jobs:read jobs:write lessons:write sessions:write"
logger = logging.getLogger(__name__)


def save_access_token(client, issuer):
    token = issue_token(client, issuer.issuer, issuer.client_secret, SCOPES)
    pending = ACCESS_TOKEN.with_suffix(".tmp")
    pending.write_text(token, encoding="ascii")
    pending.chmod(0o600)
    pending.replace(ACCESS_TOKEN)


def refresh_access_token(stop, client, issuer):
    delay = TOKEN_TTL // 2
    while not stop.wait(delay):
        try:
            save_access_token(client, issuer)
        except (httpx.HTTPError, OSError):
            logger.warning("Could not refresh the local test token. Retrying in five seconds.")
            delay = 5
        else:
            delay = TOKEN_TTL // 2


def main():
    with ExitStack() as stack:
        directory = Path(stack.enter_context(TemporaryDirectory(prefix="upraise-jwt-")))
        certificates(directory)
        issuer = DemoServer(("127.0.0.1", 0), TokenIssuer, server_tls(directory))
        issuer.issuer = f"https://127.0.0.1:{issuer.server_port}"
        test_secret = os.environ.get("UPRAISE_JWT_TEST_CLIENT_SECRET", "")
        issuer.client_secret = test_secret or secrets.token_urlsafe(32)
        issuer.signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        issuer.scopes = set(SCOPES.split())
        serve(stack, issuer)
        if test_secret:
            # Compose publishes this HTTP listener to the host's loopback only.
            token_endpoint = DemoServer(("0.0.0.0", 8001), TokenIssuer)
            token_endpoint.issuer = issuer.issuer
            token_endpoint.client_secret = issuer.client_secret
            token_endpoint.signing_key = issuer.signing_key
            token_endpoint.scopes = issuer.scopes
            serve(stack, token_endpoint)
        os.environ.update({
            "SSL_CERT_FILE": str(directory / "ca.crt"),
            "UPRAISE_OAUTH_ISSUER": issuer.issuer,
            "UPRAISE_OAUTH_AUDIENCE": AUDIENCE,
            "UPRAISE_OAUTH_JWKS_URL": issuer.issuer + "/jwks.json",
            "UPRAISE_OAUTH_CLIENTS": json.dumps([CLIENT_ID]),
        })
        client = stack.enter_context(httpx.Client(verify=client_tls(directory), trust_env=False, timeout=5))
        save_access_token(client, issuer)
        stop = Event()
        refresher = Thread(target=refresh_access_token, args=(stop, client, issuer), daemon=True)
        refresher.start()
        try:
            uvicorn.run("upraise_ai.main:app", host="0.0.0.0", port=8000, proxy_headers=False)
        finally:
            stop.set()
            refresher.join()
            ACCESS_TOKEN.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
