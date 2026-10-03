from datetime import datetime
from typing import Any

from pydantic import BaseModel


class JobStatus(BaseModel):
    job_id: str
    document_id: str
    status: str
    step: str
    progress: float
    attempts: int
    error: dict[str, Any] | None
    result: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime
    cleanup_pending: bool = False
    acknowledged: bool = False
