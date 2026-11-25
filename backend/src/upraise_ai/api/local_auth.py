"""OAuth client credentials endpoint for the development JWT test mode."""

import base64
import hmac
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from upraise_ai.core.config import Settings, get_settings
from upraise_ai.guards.ingress import (
    LOCAL_JWT_CLIENT_ID,
    LOCAL_JWT_LIFETIME,
    require_local_peer,
)

router = APIRouter()
NO_CACHE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


@router.post("/oauth/token", include_in_schema=False)
async def local_access_token(request: Request, settings: Settings = Depends(get_settings)):
    secret = settings.jwt_test_client_secret.get_secret_value()
    if not settings.local_jwt_test_mode or not secret:
        raise HTTPException(404)
    require_local_peer(request)

    scheme, _, credentials = request.headers.get("authorization", "").partition(" ")
    expected = base64.b64encode(f"{LOCAL_JWT_CLIENT_ID}:{secret}".encode())
    if scheme.lower() != "basic" or not hmac.compare_digest(credentials.encode(), expected):
        return JSONResponse(
            {"error": "invalid_client"}, status_code=401,
            headers={**NO_CACHE, "WWW-Authenticate": 'Basic realm="upraise-local"'},
        )

    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/x-www-form-urlencoded":
        return JSONResponse({"error": "invalid_request"}, status_code=400, headers=NO_CACHE)
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > 4096:
            return JSONResponse({"error": "invalid_request"}, status_code=400, headers=NO_CACHE)
        body.extend(chunk)
    try:
        form = parse_qs(body.decode("ascii"), keep_blank_values=True, max_num_fields=16)
    except ValueError:
        return JSONResponse({"error": "invalid_request"}, status_code=400, headers=NO_CACHE)
    if form.get("grant_type") != ["client_credentials"]:
        return JSONResponse({"error": "unsupported_grant_type"}, status_code=400, headers=NO_CACHE)
    if "scope" in form:
        return JSONResponse({"error": "invalid_scope"}, status_code=400, headers=NO_CACHE)

    token = request.app.state.local_jwt_issuer.issue()
    return JSONResponse(
        {"access_token": token, "token_type": "Bearer", "expires_in": LOCAL_JWT_LIFETIME},
        headers=NO_CACHE,
    )
