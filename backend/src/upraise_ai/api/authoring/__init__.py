"""Original A authoring API group."""

from fastapi import APIRouter

from .documents import router as documents_router

router = APIRouter(tags=["A - Authoring"])

router.include_router(documents_router)
