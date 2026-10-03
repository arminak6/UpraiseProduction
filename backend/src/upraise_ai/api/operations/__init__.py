"""Original C group, with only the C1 liveness route implemented."""

from fastapi import APIRouter

router = APIRouter(tags=["C - Operations"])


@router.get("/health", summary="Application liveness")
async def health() -> dict[str, str]:
    """Report application liveness without checking integrations or AI readiness."""
    return {"status": "alive"}
