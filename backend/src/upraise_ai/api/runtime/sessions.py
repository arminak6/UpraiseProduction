from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response

from upraise_ai.core.config import get_settings
from upraise_ai.guards.ingress import Principal, ingress
from upraise_ai.schemas.sessions import SessionInput, StartedSession
from upraise_ai.workflows.sessions import SessionService

router = APIRouter()


@router.post("/v1/sessions", response_model=StartedSession, status_code=201,
             responses={200: {"model": StartedSession, "description": "Existing unchanged session"}},
             summary="B1 - Start a lesson session")
def start_session(data: SessionInput, request: Request, response: Response,
                  principal: Principal = Depends(ingress), settings=Depends(get_settings),
                  session_token: str | None = Header(None, alias="X-UPRAISE-Session-JWT",
                                                     description="Signed Middleware session assertion; optional only for local preview")):
    principal.require_scope("sessions:write")
    lessons = getattr(request.app.state, "lessons", None)
    if lessons is None:
        raise HTTPException(503, "LESSON_STORAGE_NOT_CONFIGURED")
    result, created = SessionService(settings, lessons, request.app.state.sessions).start(
        data, principal, session_token,
    )
    response.status_code = 201 if created else 200
    response.headers["Cache-Control"] = "no-store"
    return result
