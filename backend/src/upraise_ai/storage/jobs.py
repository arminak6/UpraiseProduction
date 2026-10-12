from contextlib import contextmanager
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from upraise_ai.core.errors import IngestionError, LeaseLost

SCHEMA = """
CREATE TABLE IF NOT EXISTS ingestion_jobs (
    job_id UUID PRIMARY KEY,
    document_id UUID NOT NULL UNIQUE,
    client_id TEXT NOT NULL,
    idempotency_key TEXT,
    request_fingerprint TEXT NOT NULL,
    metadata JSONB NOT NULL,
    pipeline_version TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'QUEUED' CHECK (status IN ('QUEUED','RUNNING','DONE','FAILED','CANCELLED')),
    step TEXT NOT NULL DEFAULT 'ACCEPTED',
    progress DOUBLE PRECISION NOT NULL DEFAULT 0 CHECK (progress >= 0 AND progress <= 1),
    attempts INTEGER NOT NULL DEFAULT 0,
    lease_token UUID,
    lease_until TIMESTAMPTZ,
    active_attempt UUID,
    available_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    error_code TEXT,
    result JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (client_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS ingestion_jobs_pending ON ingestion_jobs (available_at, created_at)
    WHERE status IN ('QUEUED', 'RUNNING');
ALTER TABLE ingestion_jobs ADD COLUMN IF NOT EXISTS deletion_requested_at TIMESTAMPTZ;
ALTER TABLE ingestion_jobs ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;
ALTER TABLE ingestion_jobs ADD COLUMN IF NOT EXISTS cleanup_reason TEXT CHECK (cleanup_reason IN ('CANCEL','RECEIVED'));
ALTER TABLE ingestion_jobs ADD COLUMN IF NOT EXISTS cleanup_requested_at TIMESTAMPTZ;
ALTER TABLE ingestion_jobs ADD COLUMN IF NOT EXISTS cleanup_completed_at TIMESTAMPTZ;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='ingestion_jobs'::regclass
        AND conname='ingestion_jobs_status_check' AND pg_get_constraintdef(oid) NOT LIKE '%CANCELLED%') THEN
        ALTER TABLE ingestion_jobs DROP CONSTRAINT ingestion_jobs_status_check;
        ALTER TABLE ingestion_jobs ADD CONSTRAINT ingestion_jobs_status_check
            CHECK (status IN ('QUEUED','RUNNING','DONE','FAILED','CANCELLED'));
    END IF;
END $$;
CREATE INDEX IF NOT EXISTS ingestion_jobs_cleanup ON ingestion_jobs (cleanup_requested_at)
    WHERE cleanup_requested_at IS NOT NULL AND cleanup_completed_at IS NULL;
CREATE INDEX IF NOT EXISTS ingestion_jobs_course ON ingestion_jobs (client_id, (metadata->>'course_id'))
    WHERE deletion_requested_at IS NULL;
CREATE INDEX IF NOT EXISTS ingestion_jobs_deletions ON ingestion_jobs (deletion_requested_at)
    WHERE deletion_requested_at IS NOT NULL AND deleted_at IS NULL;
"""


class JobRepository:
    def __init__(self, dsn: str):
        self.dsn = dsn

    def connect(self):
        return psycopg.connect(self.dsn, row_factory=dict_row, connect_timeout=5)

    def migrate(self):
        from upraise_ai.storage.courses import SCHEMA as COURSE_SCHEMA
        with self.connect() as conn:
            conn.execute(SCHEMA)
            conn.execute(COURSE_SCHEMA)
        from upraise_ai.storage.lessons import LessonRepository

        LessonRepository(self.dsn).migrate()
        from upraise_ai.storage.operations import SCHEMA as OPERATIONS_SCHEMA

        with self.connect() as conn:
            conn.execute(OPERATIONS_SCHEMA)

    def accept(
        self, job_id, document_id, client_id, key, fingerprint, metadata, pipeline_version,
        *, legacy_fingerprint=None,
    ):
        with self.connect() as conn:
            from upraise_ai.storage.courses import admit_course
            admit_course(conn, client_id, metadata["course_id"])
            row = conn.execute(
                """INSERT INTO ingestion_jobs
                (job_id, document_id, client_id, idempotency_key, request_fingerprint, metadata, pipeline_version)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (client_id, idempotency_key) DO NOTHING RETURNING *""",
                (job_id, document_id, client_id, key, fingerprint, Jsonb(metadata), pipeline_version),
            ).fetchone()
            if row:
                return row, True
            row = conn.execute(
                "SELECT * FROM ingestion_jobs WHERE client_id=%s AND idempotency_key=%s", (client_id, key)
            ).fetchone()
            stored_fingerprint = row["request_fingerprint"]
            legacy_match = (
                legacy_fingerprint is not None
                and not stored_fingerprint.startswith("bytes-v1:")
                and stored_fingerprint == legacy_fingerprint
            )
            if stored_fingerprint != fingerprint and not legacy_match:
                raise IngestionError("IDEMPOTENCY_KEY_CONFLICT", status=409)
            return row, False

    def get(self, job_id):
        with self.connect() as conn:
            return conn.execute("SELECT * FROM ingestion_jobs WHERE job_id=%s", (job_id,)).fetchone()

    @contextmanager
    def course_admission(self, client_id, course_id):
        from upraise_ai.storage.courses import admit_course
        with self.connect() as conn:
            admit_course(conn, client_id, course_id)
            yield

    def get_document(self, document_id):
        with self.connect() as conn:
            return conn.execute("SELECT * FROM ingestion_jobs WHERE document_id=%s", (document_id,)).fetchone()

    def list_documents(self, client_id, course_id=None):
        course_filter = " AND metadata->>'course_id'=%s" if course_id is not None else ""
        parameters = (client_id, course_id) if course_id is not None else (client_id,)
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM ingestion_jobs WHERE client_id=%s" + course_filter
                + " AND deletion_requested_at IS NULL ORDER BY created_at DESC, document_id",
                parameters,
            ).fetchall()

    @contextmanager
    def job_transaction(self, job_id):
        with self.connect() as conn:
            conn.execute("SET LOCAL lock_timeout = '5s'")
            row = conn.execute(
                "SELECT * FROM ingestion_jobs WHERE job_id=%s FOR UPDATE", (job_id,)
            ).fetchone()
            yield conn, row

    def request_cleanup(self, conn, job_id, reason):
        conn.execute(
            """UPDATE ingestion_jobs SET cleanup_reason=%s, cleanup_requested_at=clock_timestamp(),
            status=CASE WHEN %s='CANCEL' THEN 'CANCELLED' ELSE status END,
            step=CASE WHEN %s='CANCEL' THEN 'CANCELLED' ELSE step END,
            active_attempt=CASE WHEN %s='CANCEL' THEN NULL ELSE active_attempt END,
            lease_token=NULL, lease_until=NULL, updated_at=clock_timestamp()
            WHERE job_id=%s AND cleanup_requested_at IS NULL""",
            (reason, reason, reason, reason, job_id),
        )

    def finish_cleanup(self, conn, job_id):
        conn.execute(
            """UPDATE ingestion_jobs SET cleanup_completed_at=clock_timestamp(), updated_at=clock_timestamp()
            WHERE job_id=%s AND cleanup_requested_at IS NOT NULL AND cleanup_completed_at IS NULL""",
            (job_id,),
        )

    def pending_cleanup(self):
        with self.connect() as conn:
            return conn.execute(
                """SELECT job_id FROM ingestion_jobs WHERE cleanup_requested_at IS NOT NULL
                AND cleanup_completed_at IS NULL ORDER BY cleanup_requested_at LIMIT 20"""
            ).fetchall()

    @contextmanager
    def document_transaction(self, document_id):
        with self.connect() as conn:
            # Bound waiting behind publication or another delete. Callers can retry.
            conn.execute("SET LOCAL lock_timeout = '5s'")
            row = conn.execute(
                "SELECT * FROM ingestion_jobs WHERE document_id=%s FOR UPDATE", (document_id,)
            ).fetchone()
            yield conn, row

    def request_deletion(self, conn, document_id):
        conn.execute(
            """UPDATE ingestion_jobs SET deletion_requested_at=clock_timestamp(),
            lease_token=NULL, lease_until=NULL, active_attempt=NULL, updated_at=clock_timestamp()
            WHERE document_id=%s AND deletion_requested_at IS NULL""",
            (document_id,),
        )

    def finish_deletion(self, conn, document_id):
        conn.execute(
            """UPDATE ingestion_jobs SET deleted_at=clock_timestamp(), updated_at=clock_timestamp()
            WHERE document_id=%s AND deletion_requested_at IS NOT NULL AND deleted_at IS NULL""",
            (document_id,),
        )

    def pending_deletion(self):
        with self.connect() as conn:
            return conn.execute(
                """SELECT document_id FROM ingestion_jobs WHERE deletion_requested_at IS NOT NULL
                AND deleted_at IS NULL ORDER BY deletion_requested_at LIMIT 1"""
            ).fetchone()

    def claim(self, lease_seconds, pipeline_version=None, *, excluded_courses=()):
        token = uuid4()
        with self.connect() as conn:
            return conn.execute(
                """WITH candidate AS (
                    SELECT job_id FROM ingestion_jobs
                    WHERE ((status='QUEUED' AND available_at <= clock_timestamp())
                       OR (status='RUNNING' AND lease_until < clock_timestamp()))
                    AND (%s::text IS NULL OR pipeline_version=%s)
                    AND deletion_requested_at IS NULL
                    AND NOT (metadata->>'course_id'=ANY(%s))
                    ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1
                ) UPDATE ingestion_jobs j SET status='RUNNING', step='PARSE',
                    attempts=attempts+1, lease_token=%s,
                    lease_until=clock_timestamp() + %s * interval '1 second',
                    error_code=NULL, updated_at=clock_timestamp()
                  FROM candidate c WHERE j.job_id=c.job_id RETURNING j.*""",
                (pipeline_version, pipeline_version, list(excluded_courses), token, lease_seconds),
            ).fetchone()

    def renew(self, job_id, token, lease_seconds):
        with self.connect() as conn:
            row = conn.execute(
                """UPDATE ingestion_jobs SET lease_until=clock_timestamp() + %s * interval '1 second',
                updated_at=clock_timestamp() WHERE job_id=%s AND lease_token=%s
                AND status='RUNNING' AND deletion_requested_at IS NULL
                AND lease_until>clock_timestamp() RETURNING job_id""",
                (lease_seconds, job_id, token),
            ).fetchone()
            return bool(row)

    def progress(self, job_id, token, step, progress):
        with self.connect() as conn:
            row = conn.execute(
                """UPDATE ingestion_jobs SET step=%s, progress=%s, updated_at=clock_timestamp()
                WHERE job_id=%s AND lease_token=%s AND status='RUNNING'
                AND deletion_requested_at IS NULL AND lease_until>clock_timestamp() RETURNING job_id""",
                (step.upper(), progress, job_id, token),
            ).fetchone()
            if not row:
                raise LeaseLost()

    @contextmanager
    def owned_transaction(self, job_id, token):
        # A reclaimed worker cannot publish or finalize while this row is locked.
        with self.connect() as conn:
            row = conn.execute(
                """SELECT *, lease_until>clock_timestamp() AS owned FROM ingestion_jobs
                WHERE job_id=%s FOR UPDATE""",
                (job_id,),
            ).fetchone()
            if (
                not row
                or row["deletion_requested_at"] is not None
                or row["status"] != "RUNNING"
                or str(row["lease_token"]) != str(token)
                or not row["owned"]
            ):
                raise LeaseLost()
            yield conn

    def done(self, conn, job_id, token, result):
        conn.execute(
            """UPDATE ingestion_jobs SET status='DONE', step='DONE', progress=1,
            result=%s, active_attempt=%s, lease_until=NULL, updated_at=clock_timestamp()
            WHERE job_id=%s AND lease_token=%s""",
            (Jsonb(result), token, job_id, token),
        )

    def fail(self, conn, job_id, token, code, retry, delay):
        conn.execute(
            """UPDATE ingestion_jobs SET status=%s, step=%s, error_code=%s, lease_until=NULL,
            available_at=clock_timestamp() + %s * interval '1 second', updated_at=clock_timestamp()
            WHERE job_id=%s AND lease_token=%s""",
            (
                "QUEUED" if retry else "FAILED",
                "RETRY_WAIT" if retry else "FAILED",
                code,
                delay,
                job_id,
                token,
            ),
        )

    def completed_before(self, days):
        with self.connect() as conn:
            return conn.execute(
                """SELECT job_id FROM ingestion_jobs WHERE (status IN ('DONE','FAILED','CANCELLED') OR deleted_at IS NOT NULL)
                AND (cleanup_requested_at IS NULL OR cleanup_completed_at IS NOT NULL)
                AND COALESCE(deleted_at, updated_at) < clock_timestamp() - %s * interval '1 day'""",
                (days,),
            ).fetchall()
