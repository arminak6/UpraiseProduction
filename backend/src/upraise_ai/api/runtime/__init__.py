"""B1 lesson start; B2-B6 remain reserved."""

from fastapi import APIRouter

from upraise_ai.api.runtime.sessions import router as sessions_router

router = APIRouter(tags=["B - Runtime"])
router.include_router(sessions_router)
