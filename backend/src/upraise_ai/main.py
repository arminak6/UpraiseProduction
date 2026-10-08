import asyncio
from contextlib import asynccontextmanager, suppress

import psycopg
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from upraise_ai.api.authoring import router as authoring_router
from upraise_ai.api.operations import router as operations_router
from upraise_ai.api.runtime import router as runtime_router
from upraise_ai.core.config import get_settings
from upraise_ai.core.errors import IngestionError
from upraise_ai.guards.body_limit import UploadLimitMiddleware
from upraise_ai.services.operations import OperationsMonitor
from upraise_ai.storage.audit import AuditRepository
from upraise_ai.storage.files import StagingStore
from upraise_ai.storage.jobs import JobRepository
from upraise_ai.storage.lessons import LessonRepository
from upraise_ai.storage.sessions import SessionStore
from upraise_ai.storage.vectors import VectorStore
from upraise_ai.workflows.documents import DocumentService
from upraise_ai.workflows.interrupts import InterruptService
from upraise_ai.workflows.jobs import JobService
from upraise_ai.workflows.lessons import LessonService
from upraise_ai.workflows.submission import SubmissionService


@asynccontextmanager
async def lifespan(app):
    settings = get_settings()
    app.state.operations = OperationsMonitor(settings)
    app.state.submission = None
    app.state.documents = None
    app.state.jobs = None
    app.state.lessons = None
    app.state.interrupts = None
    app.state.sessions = SessionStore(settings.session_max_count)
    vectors = None
    if settings.jobs_dsn.get_secret_value():
        jobs = JobRepository(settings.jobs_dsn.get_secret_value())
        staging = StagingStore(settings.data_dir)
        app.state.submission = SubmissionService(settings, jobs, staging)
        if settings.audit_dsn.get_secret_value():
            vectors = VectorStore(settings.qdrant_url, settings.qdrant_collection, settings.embedding_dimension)
            audit = AuditRepository(settings.audit_dsn.get_secret_value())
            app.state.sessions.end_audit = audit.session_ended
            app.state.documents = DocumentService(jobs, vectors, audit)
            app.state.jobs = JobService(jobs, vectors, audit, staging)
            app.state.lessons = LessonService(
                settings, LessonRepository(settings.jobs_dsn.get_secret_value()), jobs, staging, audit,
            )
            app.state.interrupts = InterruptService(settings, app.state.lessons, app.state.sessions, vectors)
    async def sweep_sessions():
        while True:
            await asyncio.sleep(settings.session_sweep_seconds)
            await asyncio.to_thread(app.state.sessions.prune)

    sweeper = asyncio.create_task(sweep_sessions())
    async def refresh_operations():
        while True:
            await asyncio.sleep(settings.operations_refresh_seconds)
            await asyncio.to_thread(app.state.operations.refresh)

    await asyncio.to_thread(app.state.operations.refresh)
    operations_task = asyncio.create_task(refresh_operations())
    try:
        yield
    finally:
        operations_task.cancel()
        with suppress(asyncio.CancelledError):
            await operations_task
        app.state.operations.close()
        sweeper.cancel()
        with suppress(asyncio.CancelledError):
            await sweeper
        app.state.sessions.clear()
        if vectors:
            vectors.close()


app = FastAPI(
    title="UPRAISE AI Module",
    version="0.1.0",
    description="UPRAISE A1-A6/A8 authoring, B1/B4/B5 learning and C1/C2 operations.",
    lifespan=lifespan,
)

app.add_middleware(UploadLimitMiddleware, max_bytes=get_settings().max_pdf_size_mb * 1024**2 + 1024**2,
                   interrupt_max_bytes=get_settings().interrupt_max_audio_bytes+65536)


@app.middleware("http")
async def local_auth_notice(request, call_next):
    response = await call_next(request)
    if get_settings().local_test_mode or getattr(request.state, "auth_mode", None) == "local-test":
        response.headers["X-UPRAISE-Auth-Mode"] = "local-test"
    return response


@app.exception_handler(IngestionError)
async def ingestion_error(request, error):
    headers = {"Retry-After": "2"} if error.code == "JOB_CLEANUP_PENDING" else None
    return JSONResponse({"detail": error.code}, status_code=error.status, headers=headers)


@app.exception_handler(ValidationError)
async def input_error(request, error):
    return JSONResponse(
        {"detail": "INVALID_DOCUMENT_METADATA", "fields": [list(e["loc"]) for e in error.errors()]},
        status_code=422,
    )


@app.exception_handler(psycopg.Error)
async def database_error(request, error):
    return JSONResponse({"detail": "JOB_STORAGE_UNAVAILABLE"}, status_code=503)


app.include_router(authoring_router)
app.include_router(runtime_router)
app.include_router(operations_router)
