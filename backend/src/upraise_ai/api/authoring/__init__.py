"""Original A authoring API group."""

from fastapi import APIRouter

from .documents import router as documents_router
from .lessons import router as lessons_router

router = APIRouter(tags=["A - Authoring"])

router.include_router(documents_router)
router.include_router(lessons_router)
