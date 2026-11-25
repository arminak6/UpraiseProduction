import ipaddress
import time
from dataclasses import dataclass
from functools import lru_cache

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPBearer

from upraise_ai.core.config import Settings, get_settings

LOCAL_JWT_CLIENT_ID = "auth-demo-middleware"
LOCAL_JWT_ISSUER = "urn:upraise:local-jwt-test"
LOCAL_JWT_AUDIENCE = "upraise-local-api"
LOCAL_JWT_LIFETIME = 3600
LOCAL_TEST_SCOPES = frozenset({
    "documents:read", "documents:write", "lessons:write", "jobs:read", "jobs:write", "sessions:write",
})


class LocalJWTIssuer:
    def __init__(self):
        self._signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.public_key = self._signing_key.public_key()

    def issue(self, scopes: list[str]) -> str:
        return jwt.encode({
            "iss": LOCAL_JWT_ISSUER,
            "aud": LOCAL_JWT_AUDIENCE,
            "client_id": LOCAL_JWT_CLIENT_ID,
            "exp": int(time.time()) + LOCAL_JWT_LIFETIME,
            "scope": " ".join(scopes),
            "upraise_courses": ["demo-course"],
        }, self._signing_key, algorithm="RS256")


@dataclass(frozen=True)
class Principal:
    client_id: str
    courses: tuple[str, ...]
    scopes: frozenset[str]
    local_test: bool = False

    def can_access_course(self, course_id: str) -> bool:
        return self.local_test or course_id in self.courses

    def require_course(self, course_id: str) -> None:
        if not self.can_access_course(course_id):
            raise HTTPException(403, "COURSE_SCOPE_DENIED")

    def require_scope(self, scope: str) -> None:
        if scope not in self.scopes:
            raise HTTPException(403, "INSUFFICIENT_SCOPE")


@lru_cache(maxsize=4)
def jwks_client(url: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(url, cache_jwk_set=True, lifespan=300, timeout=5)


bearer = HTTPBearer(auto_error=False)


def require_local_peer(request: Request) -> None:
    # Use the actual socket peer for both development modes.
    try:
        peer = ipaddress.ip_address(request.client.host if request.client else "")
        local = (peer.is_loopback or peer.is_private) and not (
            peer.is_unspecified or peer.is_multicast or peer.is_link_local
        )
    except ValueError:
        local = False
    if not local:
        raise HTTPException(403, "LOCAL_TEST_NETWORK_REQUIRED")


def require_mtls_gateway(request: Request, settings: Settings) -> None:
    if not (settings.trusted_proxy_cidrs and settings.mtls_allowed_fingerprints):
        raise HTTPException(503, "INGRESS_NOT_CONFIGURED")
    # Uvicorn proxy-header processing is disabled: client is the actual gateway.
    try:
        peer = ipaddress.ip_address(request.client.host if request.client else "")
        trusted = any(peer in ipaddress.ip_network(cidr) for cidr in settings.trusted_proxy_cidrs)
    except ValueError:
        trusted = False
    if not trusted:
        raise HTTPException(403, "TRUSTED_TLS_GATEWAY_REQUIRED")
    fingerprint = request.headers.get(settings.mtls_fingerprint_header, "").lower()
    if request.headers.get(settings.mtls_verify_header) != "SUCCESS" or fingerprint not in {
        f.lower() for f in settings.mtls_allowed_fingerprints
    }:
        raise HTTPException(403, "MTLS_REQUIRED")


def ingress(
    request: Request, settings: Settings = Depends(get_settings), token_info=Depends(bearer)
) -> Principal:
    if settings.local_test_mode or settings.local_jwt_test_mode:
        require_local_peer(request)
    if settings.local_test_mode:
        request.state.auth_mode = "local-test"
        scopes = LOCAL_TEST_SCOPES
        if settings.operations_local_admin:
            scopes = scopes | {"admin:operations"}
        return Principal(
            "local-test-client", (), scopes,
            local_test=True,
        )
    local_issuer = None
    if settings.local_jwt_test_mode and settings.jwt_test_client_secret.get_secret_value():
        local_issuer = getattr(request.app.state, "local_jwt_issuer", None)
        if local_issuer is None:
            raise HTTPException(503, "INGRESS_NOT_CONFIGURED")
    if local_issuer is None and not (
        settings.oauth_issuer
        and settings.oauth_audience
        and settings.oauth_jwks_url.startswith("https://")
        and settings.oauth_clients
    ):
        raise HTTPException(503, "INGRESS_NOT_CONFIGURED")
    if not settings.local_jwt_test_mode:
        require_mtls_gateway(request, settings)
    authorization = request.headers.get("authorization", "")
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "BEARER_TOKEN_REQUIRED", headers={"WWW-Authenticate": "Bearer"})
    token = authorization[7:]
    try:
        if local_issuer is None:
            key = jwks_client(settings.oauth_jwks_url).get_signing_key_from_jwt(token).key
            issuer, audience = settings.oauth_issuer, settings.oauth_audience
            algorithms = ["RS256", "ES256"]
            clients = settings.oauth_clients
        else:
            key = local_issuer.public_key
            issuer, audience = LOCAL_JWT_ISSUER, LOCAL_JWT_AUDIENCE
            algorithms = ["RS256"]
            clients = [LOCAL_JWT_CLIENT_ID]
        claims = jwt.decode(
            token,
            key,
            algorithms=algorithms,
            issuer=issuer,
            audience=audience,
            options={"require": ["exp", "iss", "aud"]},
        )
    except jwt.PyJWKClientConnectionError:
        raise HTTPException(503, "IDENTITY_PROVIDER_UNAVAILABLE") from None
    except jwt.PyJWTError:
        raise HTTPException(401, "INVALID_ACCESS_TOKEN", headers={"WWW-Authenticate": "Bearer"}) from None
    client_id = claims.get("client_id") or claims.get("azp") or claims.get("sub")
    courses = claims.get("upraise_courses")
    scope = claims.get("scope", "")
    if (
        client_id not in clients
        or not isinstance(courses, list)
        or not all(isinstance(c, str) for c in courses)
        or not isinstance(scope, str)
    ):
        raise HTTPException(403, "CALLER_NOT_AUTHORIZED")
    return Principal(client_id, tuple(courses), frozenset(scope.split()))
