"""B1 session start, B4 learner interruption and B5 session end."""

from fastapi import APIRouter

from upraise_ai.api.runtime.interrupts import router as interrupts_router
from upraise_ai.api.runtime.sessions import router as sessions_router

router = APIRouter(tags=["B - Runtime"])
router.include_router(sessions_router)
router.include_router(interrupts_router)
