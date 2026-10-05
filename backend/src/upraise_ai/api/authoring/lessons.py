from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from upraise_ai.guards.ingress import Principal, ingress
from upraise_ai.schemas.lessons import AcceptedLesson, LessonInput, LessonResult

router = APIRouter()


def service(request):
    value = getattr(request.app.state, "lessons", None)
    if value is None:
        raise HTTPException(503, "LESSON_STORAGE_NOT_CONFIGURED")
    return value


@router.post("/v1/lesson-jobs", response_model=AcceptedLesson, status_code=202,
             summary="A3 - Generate a draft lesson")
def generate_lesson(data: LessonInput, request: Request, response: Response,
                    principal: Principal = Depends(ingress)):
    principal.require_scope("lessons:write")
    result = service(request).submit(data, principal, request.headers.get("idempotency-key"))
    response.headers["Location"] = f"/v1/jobs/{result['job_id']}"
    return result


@router.get("/v1/jobs/{job_id}/result", response_model=LessonResult, summary="A5 - Fetch a draft lesson")
def lesson_result(job_id: UUID, request: Request, principal: Principal = Depends(ingress)):
    principal.require_scope("jobs:read")
    lessons = service(request)
    row = lessons.authorized(job_id, principal)
    with lessons.staging.artifact_lock(str(job_id)):
        return lessons.result(row)


@router.get("/v1/jobs/{job_id}/audio/{chunk_id}", summary="A5 - Download chunk audio",
            responses={200: {"content": {"audio/wav": {}}}})
def lesson_audio(job_id: UUID, chunk_id: UUID, request: Request, principal: Principal = Depends(ingress)):
    principal.require_scope("jobs:read")
    lessons = service(request)
    row = lessons.authorized(job_id, principal)
    # Open the descriptor while locked. POSIX retains an open file across unlink;
    # Windows cleanup retries while it is open. Streaming does not retain a lock
    # indefinitely when a remote client stops reading.
    with lessons.staging.artifact_lock(str(job_id)):
        path, chunk = lessons.audio(row, str(chunk_id))
        stream = path.open("rb")
        size = path.stat().st_size

    def content():
        try:
            while block := stream.read(65536):
                yield block
        finally:
            stream.close()

    return StreamingResponse(content(), media_type="audio/wav",
                             headers={"Content-Length": str(size),
                                      "Content-Disposition": f'attachment; filename="{chunk_id}.wav"',
                                      "ETag": '"' + chunk.audio_sha256 + '"', "Cache-Control": "no-store"})
