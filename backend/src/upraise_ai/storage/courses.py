"""Durable course-deletion barrier; cross-store cleanup can safely resume."""

import hashlib
import json
from contextlib import contextmanager
from uuid import uuid4

import psycopg
from fastapi import HTTPException
from psycopg.rows import dict_row

from upraise_ai.core.errors import IngestionError

SCHEMA = """
CREATE TABLE IF NOT EXISTS course_deletions (
    client_id TEXT NOT NULL, course_id TEXT NOT NULL, deletion_id UUID NOT NULL,
    requested_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), completed_at TIMESTAMPTZ,
    PRIMARY KEY(client_id,course_id)
);
"""


def lock_course(conn, client_id, course_id, *, shared=False):
    key = int.from_bytes(hashlib.sha256(json.dumps([client_id, course_id]).encode()).digest()[:8], "big", signed=True)
    conn.execute("SET LOCAL lock_timeout = '5s'")
    conn.execute("SELECT pg_advisory_xact_lock_shared(%s)" if shared else "SELECT pg_advisory_xact_lock(%s)", (key,))


def admit_course(conn, client_id, course_id):
    lock_course(conn, client_id, course_id, shared=True)
    if conn.execute("SELECT 1 FROM course_deletions WHERE client_id=%s AND course_id=%s AND completed_at IS NULL",
                    (client_id, course_id)).fetchone():
        raise IngestionError("COURSE_DELETION_PENDING", status=503, retryable=True)


class CourseRepository:
    def __init__(self, dsn):
        self.dsn = dsn

    def connect(self):
        return psycopg.connect(self.dsn, row_factory=dict_row, connect_timeout=5)

    @contextmanager
    def transaction(self, client_id, course_id):
        with self.connect() as conn:
            lock_course(conn, client_id, course_id)
            yield conn

    def begin(self, client_id, course_id):
        # Ingestion acceptance uses the matching shared lock. Commit all source
        # tombstones before locking lesson jobs (workers lock job -> source).
        with self.transaction(client_id, course_id) as conn:
            previous = self.get(conn, client_id, course_id)
            if previous and previous["completed_at"] is None:
                return previous
            exists = conn.execute("""SELECT 1 FROM ingestion_jobs WHERE client_id=%s
                AND metadata->>'course_id'=%s AND deleted_at IS NULL
                UNION ALL SELECT 1 FROM lesson_jobs WHERE client_id=%s
                AND metadata->>'course_id'=%s AND content_deletion_requested_at IS NULL LIMIT 1""",
                (client_id, course_id, client_id, course_id)).fetchone()
            if not exists:
                if previous:
                    return previous
                raise HTTPException(404, "COURSE_NOT_FOUND")
            row = conn.execute("""INSERT INTO course_deletions(client_id,course_id,deletion_id)
                VALUES (%s,%s,%s) ON CONFLICT(client_id,course_id) DO UPDATE
                SET deletion_id=EXCLUDED.deletion_id,requested_at=clock_timestamp(),completed_at=NULL RETURNING *""",
                (client_id, course_id, uuid4())).fetchone()
            conn.execute("""UPDATE ingestion_jobs SET deletion_requested_at=clock_timestamp(),
                lease_token=NULL,lease_until=NULL,active_attempt=NULL,updated_at=clock_timestamp()
                WHERE client_id=%s AND metadata->>'course_id'=%s AND deletion_requested_at IS NULL""",
                (client_id, course_id))
            return row

    @staticmethod
    def get(conn, client_id, course_id):
        return conn.execute("SELECT * FROM course_deletions WHERE client_id=%s AND course_id=%s",
                            (client_id, course_id)).fetchone()

    def documents(self, client_id, course_id):
        with self.connect() as conn:
            return conn.execute("""SELECT document_id FROM ingestion_jobs WHERE client_id=%s AND metadata->>'course_id'=%s
                ORDER BY document_id""", (client_id, course_id)).fetchall()

    def pending(self):
        with self.connect() as conn:
            return conn.execute("SELECT * FROM course_deletions WHERE completed_at IS NULL ORDER BY requested_at LIMIT 1").fetchone()
