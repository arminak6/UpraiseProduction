from contextlib import asynccontextmanager

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
from upraise_ai.storage.audit import AuditRepository
from upraise_ai.storage.files import StagingStore
from upraise_ai.storage.jobs import JobRepository
from upraise_ai.storage.vectors import VectorStore
from upraise_ai.workflows.documents import DocumentService
from upraise_ai.workflows.jobs import JobService
from upraise_ai.workflows.submission import SubmissionService


@asynccontextmanager
async def lifespan(app):
    settings = get_settings()
    app.state.submission = None
    app.state.documents = None
    app.state.jobs = None
    vectors = None
    if settings.jobs_dsn.get_secret_value():
        jobs = JobRepository(settings.jobs_dsn.get_secret_value())
        staging = StagingStore(settings.data_dir)
        app.state.submission = SubmissionService(settings, jobs, staging)
        if settings.audit_dsn.get_secret_value():
            vectors = VectorStore(settings.qdrant_url, settings.qdrant_collection, settings.embedding_dimension)
            audit = AuditRepository(settings.audit_dsn.get_secret_value())
            app.state.documents = DocumentService(jobs, vectors, audit)
            app.state.jobs = JobService(jobs, vectors, audit, staging)
    try:
        yield
    finally:
        if vectors:
            vectors.close()


app = FastAPI(
    title="UPRAISE AI Module",
    version="0.1.0",
    description="UPRAISE A1 ingestion, A2 material management, A4 monitoring and A6 job control.",
    lifespan=lifespan,
)

app.add_middleware(UploadLimitMiddleware, max_bytes=get_settings().max_pdf_size_mb * 1024**2 + 1024**2)


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
