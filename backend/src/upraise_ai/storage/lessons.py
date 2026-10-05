"""Operational lesson state only. Text, review reports and audio live on the TTL volume."""

from contextlib import contextmanager
from uuid import uuid4

from psycopg.types.json import Jsonb

from upraise_ai.core.errors import IngestionError, LeaseLost
from upraise_ai.storage.jobs import JobRepository

SCHEMA = """
CREATE TABLE IF NOT EXISTS lesson_jobs (
    job_id UUID PRIMARY KEY, client_id TEXT NOT NULL, idempotency_key TEXT,
    request_fingerprint TEXT NOT NULL, metadata JSONB NOT NULL, pipeline_version TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'QUEUED' CHECK(status IN ('QUEUED','RUNNING','DONE','FAILED','CANCELLED')),
    step TEXT NOT NULL DEFAULT 'ACCEPTED', progress DOUBLE PRECISION NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0, lease_token UUID, lease_until TIMESTAMPTZ,
    available_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), error_code TEXT, result JSONB,
    completed_at TIMESTAMPTZ, expires_at TIMESTAMPTZ,
    cleanup_reason TEXT CHECK(cleanup_reason IN ('CANCEL','RECEIVED','TTL')),
    cleanup_requested_at TIMESTAMPTZ, cleanup_completed_at TIMESTAMPTZ,
    callback_status TEXT NOT NULL DEFAULT 'NONE', callback_attempts INTEGER NOT NULL DEFAULT 0,
    callback_token UUID, callback_until TIMESTAMPTZ,
    callback_available_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), callback_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), UNIQUE(client_id,idempotency_key)
);
CREATE INDEX IF NOT EXISTS lesson_jobs_pending ON lesson_jobs(available_at,created_at)
    WHERE status IN ('QUEUED','RUNNING');
CREATE INDEX IF NOT EXISTS lesson_jobs_expiry ON lesson_jobs(expires_at)
    WHERE cleanup_completed_at IS NULL;
"""


class LessonRepository(JobRepository):
    def migrate(self):
        with self.connect() as conn:
            conn.execute(SCHEMA)

    def accept(self, client_id, key, fingerprint, metadata, pipeline_version):
        with self.connect() as conn:
            row = conn.execute(
                """INSERT INTO lesson_jobs(job_id,client_id,idempotency_key,request_fingerprint,
                metadata,pipeline_version) VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT(client_id,idempotency_key) DO NOTHING RETURNING *""",
                (uuid4(), client_id, key, fingerprint, Jsonb(metadata), pipeline_version),
            ).fetchone()
            if row:
                return row
            row = conn.execute(
                "SELECT * FROM lesson_jobs WHERE client_id=%s AND idempotency_key=%s", (client_id, key)
            ).fetchone()
            if not row or row["request_fingerprint"] != fingerprint:
                raise IngestionError("IDEMPOTENCY_KEY_CONFLICT", status=409)
            if row["cleanup_requested_at"]:
                raise IngestionError("LESSON_OUTPUT_DISPOSED", status=409)
            return row

    def get(self, job_id):
        with self.connect() as conn:
            return conn.execute("SELECT * FROM lesson_jobs WHERE job_id=%s", (job_id,)).fetchone()

    @contextmanager
    def job_transaction(self, job_id):
        with self.connect() as conn:
            conn.execute("SET LOCAL lock_timeout = '5s'")
            row = conn.execute("SELECT * FROM lesson_jobs WHERE job_id=%s FOR UPDATE", (job_id,)).fetchone()
            yield conn, row

    @contextmanager
    def owned_transaction(self, job_id, token):
        with self.job_transaction(job_id) as (conn, row):
            owned = row and conn.execute("SELECT %s::timestamptz>clock_timestamp()", (row["lease_until"],)).fetchone()
            if (not row or row["status"] != "RUNNING" or row["cleanup_requested_at"]
                    or str(row["lease_token"]) != str(token) or not owned or not next(iter(owned.values()))):
                raise LeaseLost()
            yield conn

    def claim(self, lease_seconds, pipeline_version):
        with self.connect() as conn:
            return conn.execute(
                """WITH c AS (SELECT job_id FROM lesson_jobs WHERE
                ((status='QUEUED' AND available_at<=clock_timestamp()) OR
                 (status='RUNNING' AND lease_until<clock_timestamp()))
                AND cleanup_requested_at IS NULL AND pipeline_version=%s
                ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1)
                UPDATE lesson_jobs j SET status='RUNNING', step='SOURCE', attempts=attempts+1,
                lease_token=%s, lease_until=clock_timestamp()+%s*interval '1 second',
                error_code=NULL, updated_at=clock_timestamp() FROM c WHERE j.job_id=c.job_id RETURNING j.*""",
                (pipeline_version, uuid4(), lease_seconds),
            ).fetchone()

    def renew(self, job_id, token, lease_seconds):
        with self.connect() as conn:
            return bool(conn.execute(
                """UPDATE lesson_jobs SET lease_until=clock_timestamp()+%s*interval '1 second',
                updated_at=clock_timestamp() WHERE job_id=%s AND lease_token=%s AND status='RUNNING'
                AND cleanup_requested_at IS NULL AND lease_until>clock_timestamp() RETURNING job_id""",
                (lease_seconds, job_id, token),
            ).fetchone())

    def progress(self, job_id, token, step, value):
        with self.connect() as conn:
            if not conn.execute(
                """UPDATE lesson_jobs SET step=%s,progress=%s,updated_at=clock_timestamp()
                WHERE job_id=%s AND lease_token=%s AND status='RUNNING' AND cleanup_requested_at IS NULL
                AND lease_until>clock_timestamp() RETURNING job_id""", (step, value, job_id, token)
            ).fetchone():
                raise LeaseLost()

    def wait_sources(self, conn, job_id, token, timeout_seconds, ttl):
        expired = conn.execute(
            "SELECT created_at+%s*interval '1 second'<clock_timestamp() AS expired FROM lesson_jobs WHERE job_id=%s",
            (timeout_seconds, job_id),
        ).fetchone()["expired"]
        if expired:
            self.fail(conn, job_id, token, "DOCUMENTS_NOT_READY", False, ttl)
            return
        conn.execute(
            """UPDATE lesson_jobs SET status='QUEUED', step='WAITING_FOR_INGESTION',
            error_code='DOCUMENTS_NOT_READY', attempts=greatest(0,attempts-1), lease_token=NULL,
            lease_until=NULL, available_at=clock_timestamp()+interval '10 seconds',
            updated_at=clock_timestamp() WHERE job_id=%s AND lease_token=%s""", (job_id, token)
        )

    def done(self, conn, job_id, token, result, ttl):
        conn.execute(
            """UPDATE lesson_jobs SET status='DONE',step='DONE',progress=1,result=%s,
            completed_at=clock_timestamp(), expires_at=clock_timestamp()+%s*interval '1 second',
            lease_until=NULL, callback_status=CASE WHEN metadata->>'callback_url' IS NULL
            THEN 'NONE' ELSE 'PENDING' END, updated_at=clock_timestamp()
            WHERE job_id=%s AND lease_token=%s""", (Jsonb(result), ttl, job_id, token)
        )

    def fail(self, conn, job_id, token, code, retry, ttl):
        conn.execute(
            """UPDATE lesson_jobs SET status=%s,step=%s,error_code=%s,lease_until=NULL,
            available_at=clock_timestamp()+interval '30 seconds',
            completed_at=CASE WHEN %s THEN NULL ELSE clock_timestamp() END,
            expires_at=CASE WHEN %s THEN NULL ELSE clock_timestamp()+%s*interval '1 second' END,
            callback_status=CASE WHEN %s OR metadata->>'callback_url' IS NULL THEN 'NONE' ELSE 'PENDING' END,
            updated_at=clock_timestamp() WHERE job_id=%s AND lease_token=%s""",
            ("QUEUED" if retry else "FAILED", "RETRY_WAIT" if retry else "FAILED", code,
             retry, retry, ttl, retry, job_id, token),
        )

    def maintenance(self, callback_maximum=8):
        with self.connect() as conn:
            conn.execute(
                """UPDATE lesson_jobs SET callback_status='FAILED',callback_error='CALLBACK_RETRY_LIMIT_REACHED'
                WHERE callback_status='SENDING' AND callback_until<clock_timestamp()
                AND callback_attempts>=%s""", (callback_maximum,)
            )
            conn.execute(
                """UPDATE lesson_jobs SET cleanup_reason='TTL',cleanup_requested_at=clock_timestamp(),
                callback_token=NULL,callback_until=NULL,
                callback_status=CASE WHEN callback_status IN ('PENDING','SENDING') THEN 'DISPOSED' ELSE callback_status END
                WHERE cleanup_requested_at IS NULL AND
                (expires_at<=clock_timestamp() OR (status='FAILED' AND completed_at IS NULL))"""
            )
            return conn.execute(
                """SELECT job_id FROM lesson_jobs WHERE cleanup_requested_at IS NOT NULL
                AND cleanup_completed_at IS NULL ORDER BY cleanup_requested_at LIMIT 20"""
            ).fetchall()

    def claim_callback(self, maximum):
        with self.connect() as conn:
            return conn.execute(
                """WITH c AS (SELECT job_id FROM lesson_jobs WHERE status IN ('DONE','FAILED')
                AND cleanup_requested_at IS NULL AND expires_at>clock_timestamp()
                AND callback_attempts<%s AND callback_available_at<=clock_timestamp()
                AND (callback_status='PENDING' OR (callback_status='SENDING' AND callback_until<clock_timestamp()))
                ORDER BY completed_at FOR UPDATE SKIP LOCKED LIMIT 1)
                UPDATE lesson_jobs j SET callback_status='SENDING',callback_attempts=callback_attempts+1,
                callback_token=%s,callback_until=clock_timestamp()+interval '120 seconds'
                FROM c WHERE j.job_id=c.job_id RETURNING j.*""", (maximum, uuid4())
            ).fetchone()

    def finish_callback(self, row, success, maximum):
        with self.connect() as conn:
            conn.execute(
                """UPDATE lesson_jobs SET callback_status=%s,callback_error=%s,callback_until=NULL,
                callback_available_at=clock_timestamp()+%s*interval '1 second'
                WHERE job_id=%s AND callback_token=%s AND cleanup_requested_at IS NULL""",
                ("DELIVERED" if success else "FAILED" if row["callback_attempts"] >= maximum else "PENDING",
                 None if success else "CALLBACK_DELIVERY_FAILED", min(3600, 10 * 2 ** row["callback_attempts"]),
                 row["job_id"], row["callback_token"]),
            )
