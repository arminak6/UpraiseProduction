"""B4 accepts JSON text or a bounded, RAM-spooled multipart WAV upload."""

import json
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

from upraise_ai.core.config import get_settings
from upraise_ai.guards.ingress import Principal, ingress
from upraise_ai.schemas.interrupts import InterruptInput, InterruptResponse

router = APIRouter()
INPUT_SCHEMA = InterruptInput.model_json_schema()
MULTIPART_SCHEMA = {"type": "object", "required": ["request_id", "chunk_id", "audio"],
                    "additionalProperties": False, "properties": {
                        "request_id": {"type": "string", "format": "uuid"},
                        "chunk_id": {"type": "string", "format": "uuid"},
                        "audio": {"type": "string", "format": "binary", "description": "PCM mono 16-bit WAV, 8–48 kHz, at most 60 s"},
                        "playback_offset_ms": {"type": "integer", "minimum": 0}}}


@router.post("/v1/sessions/{session_id}/interrupts", response_model=InterruptResponse,
             summary="B4 - Handle a learner interruption",
             openapi_extra={"requestBody": {"required": True, "content": {
                 "application/json": {"schema": {**INPUT_SCHEMA, "required": ["request_id", "chunk_id", "text"]}},
                 "multipart/form-data": {"schema": MULTIPART_SCHEMA}}}})
async def interrupt(session_id: UUID, request: Request, response: Response,
                    principal: Principal = Depends(ingress), settings=Depends(get_settings),
                    session_token: str | None = Header(None, alias="X-UPRAISE-Session-JWT")):
    service = getattr(request.app.state, "interrupts", None)
    if service is None:
        raise HTTPException(503, "INTERRUPT_STORAGE_NOT_CONFIGURED")
    # Authenticate before reading/parsing uploaded learner content.
    await run_in_threadpool(service.authorized_session, session_id, principal, session_token)
    if not service.input_capacity.acquire(blocking=False):
        raise HTTPException(503, "INTERRUPT_CAPACITY_REACHED", headers={"Retry-After": "2"})
    try:
        data, audio = await read_input(request, settings)
        value = await run_in_threadpool(service.handle, session_id, data, principal, session_token, audio)
        response.headers["Cache-Control"] = "no-store"
        return value
    finally:
        service.input_capacity.release()


async def read_input(request, settings):
    media_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    audio = None
    try:
        if media_type == "application/json":
            raw = await request.body()
            if len(raw) > 32000:
                raise HTTPException(413, "INTERRUPT_MESSAGE_TOO_LARGE")
            data = InterruptInput.model_validate_json(raw)
            if data.text is None:
                raise HTTPException(422, "SEND_EXACTLY_ONE_TEXT_OR_AUDIO")
        elif media_type == "multipart/form-data":
            class MemoryParser(MultiPartParser):
                spool_max_size = settings.interrupt_max_audio_bytes + 65536

            parser = MemoryParser(request.headers, request.stream(), max_files=1, max_fields=3, max_part_size=32000)
            form = await parser.parse()
            try:
                fields = form.multi_items()
                if len({key for key, _ in fields}) != len(fields) or set(form) - {"request_id", "chunk_id", "audio", "playback_offset_ms"}:
                    raise HTTPException(422, "INVALID_INTERRUPT_FIELDS")
                upload = form.get("audio")
                if not isinstance(upload, UploadFile) or not upload.filename:
                    raise HTTPException(422, "SEND_EXACTLY_ONE_TEXT_OR_AUDIO")
                audio = await upload.read(settings.interrupt_max_audio_bytes+1)
                if len(audio) > settings.interrupt_max_audio_bytes:
                    raise HTTPException(413, "INTERRUPT_AUDIO_TOO_LARGE")
                values = {key: value for key, value in fields if key != "audio"}
                if "playback_offset_ms" in values:
                    values["playback_offset_ms"] = int(values["playback_offset_ms"])
                data = InterruptInput.model_validate(values)
            finally:
                await form.close()
        else:
            raise HTTPException(415, "USE_JSON_TEXT_OR_MULTIPART_WAV")
    except (ValidationError, ValueError, json.JSONDecodeError, MultiPartException):
        raise HTTPException(422, "INVALID_INTERRUPT_INPUT") from None
    return data, audio
