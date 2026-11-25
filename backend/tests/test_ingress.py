import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from pydantic import ValidationError
from starlette.requests import Request

from upraise_ai.core.config import Settings
from upraise_ai.guards.ingress import ingress


def test_jwt_test_mode_requires_development_and_cannot_skip_authentication():
    with pytest.raises(ValidationError, match="local_jwt_test_mode requires environment=development"):
        Settings(environment="production", local_jwt_test_mode=True)
    with pytest.raises(ValidationError, match="mutually exclusive"):
        Settings(environment="development", local_test_mode=True, local_jwt_test_mode=True)


def test_oauth_signature_expiry_and_mtls_gateway_without_scopes(monkeypatch):
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(
        "upraise_ai.guards.ingress.jwks_client",
        lambda _: SimpleNamespace(
            get_signing_key_from_jwt=lambda _: SimpleNamespace(key=private.public_key())
        ),
    )
    settings = Settings(
        oauth_issuer="https://issuer.example",
        oauth_audience="upraise",
        oauth_jwks_url="https://issuer.example/keys",
        oauth_clients=["middleware"],
        trusted_proxy_cidrs=["10.0.0.2/32"],
        mtls_allowed_fingerprints=["verified-cert"],
    )
    claims = {
        "iss": settings.oauth_issuer,
        "aud": "upraise",
        "exp": int(time.time()) + 60,
        "client_id": "middleware",
        "upraise_courses": ["25"],
    }

    def request(token, peer="10.0.0.2", certificate="verified-cert"):
        return Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/v1/documents",
                "client": (peer, 443),
                "headers": [
                    (b"authorization", ("Bearer " + token).encode()),
                    (b"x-ssl-client-verify", b"SUCCESS"),
                    (b"x-ssl-client-fingerprint", certificate.encode()),
                ],
            }
        )

    token = jwt.encode(claims, private, algorithm="RS256")
    principal = ingress(request(token), settings)
    assert principal.client_id == "middleware"
    principal.require_course("25")
    assert not principal.scopes
    jwt_test = Settings(
        environment="development", local_jwt_test_mode=True, local_test_mode=False,
        oauth_issuer=settings.oauth_issuer, oauth_audience=settings.oauth_audience,
        oauth_jwks_url=settings.oauth_jwks_url, oauth_clients=settings.oauth_clients,
    )
    assert ingress(request(token, certificate=""), jwt_test) == principal
    with pytest.raises(HTTPException) as denied:
        ingress(request(token, peer="8.8.8.8"), jwt_test)
    assert (denied.value.status_code, denied.value.detail) == (403, "LOCAL_TEST_NETWORK_REQUIRED")
    legacy_claims = dict(claims, iat=int(time.time()), sub="middleware")
    del legacy_claims["client_id"]
    legacy_token = jwt.encode(legacy_claims, private, algorithm="RS256")
    assert ingress(request(legacy_token), settings) == principal
    no_identity_claims = {key: value for key, value in claims.items() if key != "client_id"}
    no_identity = jwt.encode(no_identity_claims, private, algorithm="RS256")
    with pytest.raises(HTTPException) as denied:
        ingress(request(no_identity), settings)
    assert (denied.value.status_code, denied.value.detail) == (403, "CALLER_NOT_AUTHORIZED")
    with pytest.raises(HTTPException) as denied:
        principal.require_course("another-course")
    assert denied.value.status_code == 403
    for invalid in (request(token, peer="10.0.0.3"), request(token, certificate="forged-cert")):
        with pytest.raises(HTTPException) as denied:
            ingress(invalid, settings)
        assert denied.value.status_code == 403
    expired = jwt.encode({**claims, "exp": int(time.time()) - 1}, private, algorithm="RS256")
    with pytest.raises(HTTPException) as denied:
        ingress(request(expired), settings)
    assert denied.value.status_code == 401
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    forged = jwt.encode(claims, other, algorithm="RS256")
    with pytest.raises(HTTPException) as denied:
        ingress(request(forged), settings)
    assert denied.value.status_code == 401
