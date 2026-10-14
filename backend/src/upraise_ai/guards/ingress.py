import ipaddress
from dataclasses import dataclass
from functools import lru_cache

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPBearer

from upraise_ai.core.config import Settings, get_settings


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


def ingress(
    request: Request, settings: Settings = Depends(get_settings), token_info=Depends(bearer)
) -> Principal:
    if settings.local_test_mode:
        # Explicit development opt-in only; never use forwarded client headers.
        try:
            peer = ipaddress.ip_address(request.client.host if request.client else "")
            local = (peer.is_loopback or peer.is_private) and not (
                peer.is_unspecified or peer.is_multicast or peer.is_link_local
            )
        except ValueError:
            local = False
        if not local:
            raise HTTPException(403, "LOCAL_TEST_NETWORK_REQUIRED")
        request.state.auth_mode = "local-test"
        scopes = {"documents:write", "documents:read", "lessons:write", "jobs:read", "jobs:write", "sessions:write"}
        if settings.operations_local_admin:
            scopes.add("admin:operations")
        return Principal(
            "local-test-client", (), frozenset(scopes),
            local_test=True,
        )
    if not (
        settings.oauth_issuer
        and settings.oauth_audience
        and settings.oauth_jwks_url.startswith("https://")
        and settings.oauth_clients
        and settings.trusted_proxy_cidrs
        and settings.mtls_allowed_fingerprints
    ):
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
    authorization = request.headers.get("authorization", "")
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "BEARER_TOKEN_REQUIRED", headers={"WWW-Authenticate": "Bearer"})
    token = authorization[7:]
    try:
        key = jwks_client(settings.oauth_jwks_url).get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256", "ES256"],
            issuer=settings.oauth_issuer,
            audience=settings.oauth_audience,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
    except jwt.PyJWKClientConnectionError:
        raise HTTPException(503, "IDENTITY_PROVIDER_UNAVAILABLE") from None
    except jwt.PyJWTError:
        raise HTTPException(401, "INVALID_ACCESS_TOKEN", headers={"WWW-Authenticate": "Bearer"}) from None
    client_id = claims.get("client_id") or claims.get("azp") or claims.get("sub")
    courses = claims.get("upraise_courses")
    scope = claims.get("scope", "")
    if (
        client_id not in settings.oauth_clients
        or not isinstance(courses, list)
        or not all(isinstance(c, str) for c in courses)
        or not isinstance(scope, str)
    ):
        raise HTTPException(403, "CALLER_NOT_AUTHORIZED")
    return Principal(client_id, tuple(courses), frozenset(scope.split()))
