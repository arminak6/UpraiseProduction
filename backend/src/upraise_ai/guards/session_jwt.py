"""Signed Middleware session assertions, in addition to service INGRESS."""

import jwt
from fastapi import HTTPException
from pydantic import ValidationError

from upraise_ai.guards.ingress import jwks_client
from upraise_ai.schemas.sessions import SessionClaims


def validate_session_jwt(token, settings):
    if not token or len(token) > 16384:
        raise HTTPException(401, "SESSION_JWT_REQUIRED" if not token else "INVALID_SESSION_JWT")
    if not (settings.session_jwt_issuer and settings.session_jwt_audience
            and settings.session_jwt_jwks_url.startswith("https://")
            and settings.session_jwt_audience != settings.oauth_audience):
        raise HTTPException(503, "SESSION_AUTH_NOT_CONFIGURED")
    try:
        key = jwks_client(settings.session_jwt_jwks_url).get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token, key, algorithms=["RS256", "ES256"], issuer=settings.session_jwt_issuer,
            audience=settings.session_jwt_audience,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
        return SessionClaims.model_validate(claims)
    except jwt.PyJWKClientConnectionError:
        raise HTTPException(503, "SESSION_IDENTITY_PROVIDER_UNAVAILABLE") from None
    except (jwt.PyJWTError, ValidationError):
        raise HTTPException(401, "INVALID_SESSION_JWT") from None
