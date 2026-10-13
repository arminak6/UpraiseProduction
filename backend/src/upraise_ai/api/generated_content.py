"""B7 author-managed generated teaching content, with independent source lifecycle."""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response

from upraise_ai.api.authoring.lessons import service
from upraise_ai.core.errors import IngestionError
from upraise_ai.guards.ingress import Principal, ingress
from upraise_ai.workflows.lesson_deletion import begin_generated_deletion

router = APIRouter(tags=["B - Generated content"])


@router.delete("/v1/lesson-jobs/{lesson_job_id}", status_code=204,
               summary="B7 - Delete generated teaching content")
def delete_generated_content(lesson_job_id: UUID, request: Request, principal: Principal = Depends(ingress),
                             learner_level: str | None = Query(None, min_length=1, max_length=40,
                                                               pattern=r"^[a-z][a-z0-9_]*$")):
    principal.require_scope("lessons:write")
    lessons, job_id = service(request), str(lesson_job_id)
    pending = begin_generated_deletion(lessons, job_id, learner_level, principal)
    if pending:
        sessions = getattr(request.app.state, "sessions", None)
        if sessions:
            sessions.revoke_jobs([job_id])
        try:
            lessons.complete_cleanup(job_id)
        except OSError:
            raise IngestionError("GENERATED_CONTENT_CLEANUP_PENDING", status=503, retryable=True) from None
    return Response(status_code=204)
