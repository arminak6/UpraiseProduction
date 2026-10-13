import os
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from upraise_ai.guards.ingress import Principal, ingress
from upraise_ai.schemas.aligned_lessons import AlignedLessonInput, AlignedLessonResult
from upraise_ai.schemas.lesson_catalog import ReadyLesson
from upraise_ai.schemas.lessons import AcceptedLesson, LessonInput, LessonResult
from upraise_ai.schemas.revisions import ChunkRevisionInput
from upraise_ai.workflows.revisions import submit_revision

router = APIRouter()


def service(request):
    value = getattr(request.app.state, "lessons", None)
    if value is None:
        raise HTTPException(503, "LESSON_STORAGE_NOT_CONFIGURED")
    return value


@router.get("/v1/lesson-jobs", response_model=list[ReadyLesson], response_model_exclude_none=True,
            summary="A8 - List ready generated lessons")
def ready_lessons(request: Request, response: Response,
                  course_id: str | None = Query(None, min_length=1, max_length=120),
                  limit: int = Query(100, ge=1, le=100), offset: int = Query(0, ge=0, le=1_000_000),
                  principal: Principal = Depends(ingress)):
    principal.require_scope("jobs:read")
    response.headers["Cache-Control"] = "no-store"
    return service(request).list_ready(principal, course_id=course_id, limit=limit, offset=offset)


@router.post("/v1/lesson-jobs", response_model=AcceptedLesson, status_code=202,
             summary="A3 - Generate a draft lesson")
def generate_lesson(data: LessonInput, request: Request, response: Response,
                    principal: Principal = Depends(ingress)):
    principal.require_scope("lessons:write")
    result = service(request).submit(data, principal, request.headers.get("idempotency-key"))
    response.headers["Location"] = f"/v1/jobs/{result['job_id']}"
    return result


@router.post('/v2/lesson-jobs', response_model=AcceptedLesson, status_code=202,
             summary='A3 v2 - Generate all levels using one shared chunk plan')
def generate_aligned_lesson(data: AlignedLessonInput, request: Request, response: Response,
                            principal: Principal = Depends(ingress)):
    principal.require_scope('lessons:write')
    result = service(request).submit(data, principal, request.headers.get('idempotency-key'))
    response.headers['Location'] = f"/v1/jobs/{result['job_id']}"
    return result


@router.get("/v1/jobs/{job_id}/result", response_model=AlignedLessonResult | LessonResult, response_model_exclude_none=True,
            summary="A5 - Fetch a draft lesson")
def lesson_result(job_id: UUID, request: Request, response: Response, principal: Principal = Depends(ingress),
                  learner_level: str | None = Query(None, min_length=1, max_length=40, pattern=r'^[a-z][a-z0-9_]*$')):
    principal.require_scope("jobs:read")
    lessons = service(request)
    row, replacement_level = lessons.delivery_row(job_id, principal)
    learner_level = learner_level or replacement_level
    with lessons.staging.artifact_lock(str(row["job_id"]), shared=True):
        result = lessons.result(row, learner_level=learner_level) if learner_level else lessons.result(row)
        lessons.audit_download(row)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/v1/audio:regenerate", response_model=AcceptedLesson, status_code=202,
             summary="A7 - Revise one teaching chunk and regenerate its audio")
def regenerate_audio(data: ChunkRevisionInput, request: Request, response: Response,
                     principal: Principal = Depends(ingress)):
    principal.require_scope("lessons:write")
    # Request idempotency is explicitly deferred for A7. Every submission is new.
    result = submit_revision(service(request), data, principal)
    response.headers["Location"] = f"/v1/jobs/{result['job_id']}"
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/v1/jobs/{job_id}/audio/{chunk_id}", summary="A5 - Download chunk audio",
            responses={200: {"content": {"audio/wav": {}}}})
def lesson_audio(job_id: UUID, chunk_id: UUID, request: Request, principal: Principal = Depends(ingress)):
    principal.require_scope("jobs:read")
    lessons = service(request)
    row, _ = lessons.delivery_row(job_id, principal)
    # Open the descriptor while locked. POSIX retains an open file across unlink;
    # Windows cleanup retries while it is open. Streaming does not retain a lock
    # indefinitely when a remote client stops reading.
    with lessons.staging.artifact_lock(str(row["job_id"]), shared=True):
        path, chunk = lessons.audio(row, str(chunk_id))
        stream = path.open("rb")
        try:
            size = os.fstat(stream.fileno()).st_size
            lessons.audit_download(row, chunk=chunk, byte_count=size)
        except BaseException:
            stream.close()
            raise

    def content():
        try:
            while block := stream.read(65536):
                yield block
        finally:
            stream.close()

    return StreamingResponse(content(), media_type="audio/wav", background=BackgroundTask(stream.close),
                             headers={"Content-Length": str(size),
                                      "Content-Disposition": f'attachment; filename="{chunk_id}.wav"',
                                      "ETag": '"' + chunk.audio_sha256 + '"', "Cache-Control": "no-store"})
