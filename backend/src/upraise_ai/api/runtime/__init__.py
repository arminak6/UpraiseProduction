"""B1 session start and B4 learner interruption."""

from fastapi import APIRouter

from upraise_ai.api.runtime.interrupts import router as interrupts_router
from upraise_ai.api.runtime.sessions import router as sessions_router

router = APIRouter(tags=["B - Runtime"])
router.include_router(sessions_router)
router.include_router(interrupts_router)
