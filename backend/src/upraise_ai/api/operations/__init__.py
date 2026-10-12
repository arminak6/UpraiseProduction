"""C1 liveness/readiness, C2 feature status and C5 deployed versions."""

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from upraise_ai.guards.ingress import Principal, ingress
from upraise_ai.schemas.operations import ReadinessStatus, ServiceStatus
from upraise_ai.schemas.versions import VersionStatus

router = APIRouter(tags=["C - Operations"])


@router.get("/health", summary="Application liveness")
async def health() -> dict[str, str]:
    """Report application liveness without checking integrations or AI readiness."""
    return {"status": "alive"}


def monitor(request):
    value = getattr(request.app.state, "operations", None)
    if value is None:
        raise HTTPException(503, "OPERATIONS_NOT_CONFIGURED")
    return value


@router.get("/ready", response_model=ReadinessStatus, summary="C1 - Dependency readiness",
            responses={503: {"model": ReadinessStatus}})
def ready(request: Request, response: Response):
    result = monitor(request).snapshot()
    response.headers["Cache-Control"] = "no-store"
    if result.status == "NOT_READY":
        response.status_code = 503
    return result


@router.get("/v1/status", response_model=ServiceStatus, summary="C2 - AI feature availability")
def service_status(request: Request, response: Response,
                   course_id: str | None = Query(None, min_length=1, max_length=120),
                   principal: Principal = Depends(ingress)):
    principal.require_scope("jobs:read")
    if course_id is not None:
        if not course_id.strip() or course_id != course_id.strip():
            raise HTTPException(422, "INVALID_COURSE_ID")
        principal.require_course(course_id)
    response.headers["Cache-Control"] = "no-store"
    return monitor(request).status(course_id)


@router.get("/v1/version", response_model=VersionStatus, summary="C5 - Deployed model, prompt and pipeline versions")
def deployed_version(request: Request, response: Response, principal: Principal = Depends(ingress)):
    principal.require_scope("jobs:read")
    catalog = getattr(request.app.state, "versions", None)
    if catalog is None:
        raise HTTPException(503, "VERSION_METADATA_NOT_CONFIGURED")
    response.headers["Cache-Control"] = "no-store"
    return catalog.snapshot()
