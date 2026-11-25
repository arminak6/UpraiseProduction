import asyncio
import json
from pathlib import PurePosixPath
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi import Path as PathParameter
from starlette.datastructures import UploadFile

from upraise_ai.core.config import get_settings
from upraise_ai.core.errors import IngestionError
from upraise_ai.core.utils import sha256_file
from upraise_ai.guards.ingress import Principal, ingress
from upraise_ai.schemas.documents import (
    AcceptedDocument,
    DocumentInput,
    DocumentSummary,
    IngestionMetadata,
    UrlDocumentInput,
)
from upraise_ai.schemas.jobs import JobStatus
from upraise_ai.services.moodle import download_pdf

router = APIRouter()
FIELDS = {"course_id", "activity_id", "language", "source_ref", "content_hash", "version"}
POWERPOINT_FIELDS = {"include_speaker_notes", "include_hidden_slides"}
MULTIPART_SCHEMA = {
    "type": "object",
    "required": ["file", "course_id", "activity_id", "language", "source_ref", "content_hash"],
    "properties": {**{f: {"type": "string"} for f in FIELDS},
                   **{f: {"type": "boolean", "default": False} for f in POWERPOINT_FIELDS},
                   "file": {"type": "string", "format": "binary"}},
}


def service(request):
    submission = getattr(request.app.state, "submission", None)
    if submission is None:
        raise HTTPException(503, "INGESTION_STORAGE_NOT_CONFIGURED")
    return submission


def document_service(request):
    documents = getattr(request.app.state, "documents", None)
    if documents is None:
        raise HTTPException(503, "DOCUMENT_STORAGE_NOT_CONFIGURED")
    return documents


@router.delete("/v1/jobs/{job_id}", status_code=204, summary="A6 - Cancel or acknowledge a job")
def dispose_job(
    job_id: UUID, request: Request, reason: Literal["CANCEL", "RECEIVED"] = Query(),
    principal: Principal = Depends(ingress),
):
    lessons = getattr(request.app.state, "lessons", None)
    if lessons is not None and lessons.jobs.get(str(job_id)):
        lessons.dispose(str(job_id), reason, principal)
        return Response(status_code=204)
    jobs = getattr(request.app.state, "jobs", None)
    if jobs is None:
        raise HTTPException(503, "JOB_CONTROL_NOT_CONFIGURED")
    jobs.dispose(str(job_id), reason, principal)
    return Response(status_code=204)


@router.get("/v1/documents", response_model=list[DocumentSummary], summary="A2 – List ingested material")
def list_documents(
    request: Request, course_id: str | None = Query(None, min_length=1, max_length=120),
    principal: Principal = Depends(ingress),
):
    return document_service(request).list(course_id, principal)


@router.delete("/v1/documents/{document_id}", status_code=204, summary="A2 – Remove ingested material")
def delete_document(document_id: UUID, request: Request, principal: Principal = Depends(ingress)):
    document_service(request).delete(str(document_id), principal)
    return Response(status_code=204)


@router.get('/v1/documents/{document_id}/slides/{slide_number}', summary='Read a retained PowerPoint source slide')
def source_slide(document_id: UUID, slide_number: int, request: Request, document_version_id: UUID = Query(),
                 principal: Principal = Depends(ingress)):
    documents = document_service(request)
    row = documents.jobs.get_document(str(document_id))
    documents.authorize(row, principal)
    staging = service(request).staging
    with staging.artifact_lock(str(row['job_id']), shared=True):
        with documents.jobs.document_transaction(str(document_id)) as (_, current):
            documents.authorize(current, principal)
            if (current['deletion_requested_at'] or current['status'] != 'DONE'
                    or current['metadata'].get('source_format') != 'pptx' or not 1 <= slide_number <= 5000):
                raise HTTPException(404, 'SLIDE_PREVIEW_UNAVAILABLE')
            folder = staging.job_dir(str(current['job_id'])) / 'powerpoint-artifacts'
            try:
                manifest = json.loads((folder / 'slides.json').read_bytes())
                slide = next((s for s in manifest['slides'] if s['slide_number'] == slide_number), None)
                if (not slide or manifest['document_id'] != str(document_id)
                        or manifest['document_version_id'] != str(document_version_id)
                        or manifest['pipeline_version'] != current['pipeline_version']):
                    raise HTTPException(404, 'SLIDE_PREVIEW_UNAVAILABLE')
                path = folder / f'slide-{slide_number}.png'
                if path.stat().st_size > 12_000_000 or sha256_file(path) != slide['image_sha256']:
                    raise HTTPException(503, 'SLIDE_PREVIEW_INTEGRITY_FAILURE')
                data = path.read_bytes()
            except (FileNotFoundError, KeyError, ValueError, StopIteration):
                raise HTTPException(404, 'SLIDE_PREVIEW_UNAVAILABLE') from None
            return Response(data, media_type='image/png', headers={'Cache-Control': 'no-store'})


@router.delete("/v1/courses/{course_id:path}", status_code=204, summary="A2 - Remove a course and its generated lessons")
def delete_course(request: Request, course_id: str = PathParameter(min_length=1, max_length=120),
                  principal: Principal = Depends(ingress)):
    document_service(request).delete_course(course_id, principal)
    return Response(status_code=204)


@router.post(
    "/v1/documents",
    status_code=202,
    response_model=AcceptedDocument,
    summary="A1 – Ingest teaching material",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {"schema": MULTIPART_SCHEMA},
                "application/json": {"schema": UrlDocumentInput.model_json_schema()},
            },
        }
    },
)
async def ingest_document(request: Request, response: Response, principal: Principal = Depends(ingress)):
    submission = service(request)
    key = request.headers.get("idempotency-key")
    content_type = request.headers.get("content-type", "").split(";")[0].lower()
    if content_type == "multipart/form-data":
        async with request.form(max_files=1, max_fields=8, max_part_size=8192) as form:
            if set(form) - (FIELDS | POWERPOINT_FIELDS | {"file"}) or any(len(form.getlist(k)) != 1 for k in form):
                raise IngestionError("UNEXPECTED_OR_DUPLICATE_FIELD", status=400)
            upload = form.get("file")
            if not isinstance(upload, UploadFile) or not upload.filename:
                raise IngestionError("DOCUMENT_UPLOAD_REQUIRED", status=400)
            filename = PurePosixPath(upload.filename.replace("\\", "/")).name
            data = DocumentInput.model_validate({k: form[k] for k in FIELDS if k in form})
            principal.require_course(data.course_id)
            from upraise_ai.services.formats import upload_format

            source_format = upload_format(filename, upload.content_type)
            options = {key: form[key] for key in POWERPOINT_FIELDS if key in form}
            if options and (source_format != 'pptx' or any(value not in {'true', 'false'} for value in options.values())):
                raise IngestionError('INVALID_POWERPOINT_OPTIONS', status=422)
            metadata = IngestionMetadata(**data.model_dump(), source_filename=filename, source_format=source_format,
                                         **{key: value == 'true' for key, value in options.items()})
            result = await asyncio.to_thread(submission.submit, metadata, upload.file, principal, key)
    elif content_type == "application/json":
        try:
            data = UrlDocumentInput.model_validate(await request.json())
        except ValueError as error:
            from pydantic import ValidationError

            if isinstance(error, ValidationError):
                raise
            raise IngestionError("INVALID_JSON", status=400) from None
        principal.require_course(data.course_id)
        metadata = IngestionMetadata(
            **data.model_dump(exclude={"moodle_url"}), source_filename="moodle-source.pdf"
        )

        def download_and_submit():
            settings = get_settings()
            with download_pdf(data.moodle_url, settings.moodle_allowed_cidrs) as stream:
                return submission.submit(metadata, stream, principal, key)

        result = await asyncio.to_thread(download_and_submit)
    else:
        raise IngestionError("UNSUPPORTED_CONTENT_TYPE", status=415)
    response.headers["Location"] = f"/v1/jobs/{result['job_id']}"
    return {"document_id": result["document_id"], "job_id": result["job_id"]}


@router.get("/v1/jobs/{job_id}", response_model=JobStatus, summary="A4 – Job status")
def job_status(job_id: UUID, request: Request, principal: Principal = Depends(ingress)):
    lessons = getattr(request.app.state, "lessons", None)
    if lessons is not None and lessons.jobs.get(str(job_id)):
        row = lessons.authorized(job_id, principal)
        return JobStatus(
            job_id=str(row["job_id"]), lesson_id=row["metadata"]["lesson_id"], status=row["status"],
            step=row["step"], progress=row["progress"], attempts=row["attempts"],
            error={"code": row["error_code"], **((row.get("result") or {}).get("revision_failure", {})
                    if row["error_code"] in {"REVISION_ALIGNMENT_REJECTED", "REVISION_MODEL_INVALID_OUTPUT"}
                    else (row.get("result") or {}).get("teaching_failure", {})
                    if row["error_code"] in {"TEACHING_V2_MATERIAL_REJECTED", "TEACHING_V2_REVIEW_EVIDENCE_INVALID"} else {})} if row["error_code"] else None,
            result=row["result"],
            created_at=row["created_at"], updated_at=row["updated_at"], expires_at=row["expires_at"],
            cleanup_pending=bool(row["cleanup_requested_at"] and not row["cleanup_completed_at"]),
            acknowledged=bool(row["cleanup_reason"] == "RECEIVED" and row["cleanup_completed_at"]),
            callback_status=row["callback_status"], callback_attempts=row["callback_attempts"],
            callback_error=row["callback_error"],
        )
    row = service(request).jobs.get(str(job_id))
    if (
        not row
        or row["deletion_requested_at"] is not None
        or row["client_id"] != principal.client_id
        or not principal.can_access_course(row["metadata"]["course_id"])
    ):
        raise HTTPException(404, "JOB_NOT_FOUND")
    return JobStatus(
        job_id=str(row["job_id"]),
        document_id=str(row["document_id"]),
        status=row["status"],
        step=row["step"],
        progress=row["progress"],
        attempts=row["attempts"],
        error={"code": row["error_code"]} if row["error_code"] else None,
        result=row["result"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        cleanup_pending=bool(row["cleanup_requested_at"] and not row["cleanup_completed_at"]),
        acknowledged=bool(row["cleanup_reason"] == "RECEIVED" and row["cleanup_completed_at"]),
    )
