from uuid import NAMESPACE_URL, uuid4, uuid5

import psycopg
from psycopg.types.json import Jsonb

SCHEMA = """
CREATE TABLE IF NOT EXISTS ingestion_audit (
    event_id UUID PRIMARY KEY,
    job_id UUID NOT NULL,
    document_id UUID NOT NULL,
    event_type TEXT NOT NULL,
    details JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX IF NOT EXISTS ingestion_audit_job ON ingestion_audit (job_id, created_at);
CREATE TABLE IF NOT EXISTS lesson_audit (
    event_id UUID PRIMARY KEY, job_id UUID NOT NULL, event_type TEXT NOT NULL,
    details JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX IF NOT EXISTS lesson_audit_job ON lesson_audit(job_id, created_at);
"""


class AuditRepository:
    def __init__(self, dsn):
        self.dsn = dsn

    def migrate(self):
        with psycopg.connect(self.dsn, connect_timeout=5) as conn:
            conn.execute(SCHEMA)

    def record(self, job, event_type, details, *, attempt=False):
        identity = f"upraise:{job['job_id']}:{event_type}"
        if attempt:
            identity += f":{job['lease_token']}"
        event_id = uuid5(NAMESPACE_URL, identity)
        # No document text, filenames, URLs, credentials or learner information.
        with psycopg.connect(self.dsn, connect_timeout=5) as conn:
            conn.execute(
                """INSERT INTO ingestion_audit (event_id,job_id,document_id,event_type,details)
                VALUES (%s,%s,%s,%s,%s) ON CONFLICT (event_id) DO NOTHING""",
                (event_id, job["job_id"], job["document_id"], event_type, Jsonb(details)),
            )

    def lesson(self, job, event_type, details, *, attempt=False):
        identity = f"lesson:{job['job_id']}:{event_type}"
        if attempt:
            identity += f":{job['lease_token']}"
        with psycopg.connect(self.dsn, connect_timeout=5) as conn:
            conn.execute(
                """INSERT INTO lesson_audit(event_id,job_id,event_type,details) VALUES (%s,%s,%s,%s)
                ON CONFLICT(event_id) DO NOTHING""",
                (uuid5(NAMESPACE_URL, identity), job["job_id"], event_type, Jsonb(details)),
            )

    def lesson_access(self, job, event_type, details):
        # Access is per request, unlike idempotent lifecycle events. Only artifact
        # IDs/hashes/counts are recorded; no lesson content or credentials.
        with psycopg.connect(self.dsn, connect_timeout=5) as conn:
            conn.execute(
                """INSERT INTO lesson_audit(event_id,job_id,event_type,details)
                VALUES (%s,%s,%s,%s)""",
                (uuid4(), job["job_id"], event_type, Jsonb(details)),
            )

    def session_started(self, job, event_id, details):
        # Pseudonymous session UUID + content-free version metadata only. No JWT,
        # learner identity, chat, telemetry or lesson text is persisted.
        with psycopg.connect(self.dsn, connect_timeout=5, options="-c statement_timeout=5000") as conn:
            conn.execute(
                """INSERT INTO lesson_audit(event_id,job_id,event_type,details)
                VALUES (%s,%s,'session_started',%s) ON CONFLICT(event_id) DO NOTHING""",
                (event_id, job["job_id"], Jsonb(details)),
            )

    def learner_interrupt(self, job, session_id, request_id, details):
        event_id = uuid5(NAMESPACE_URL, f"interrupt:{session_id}:{request_id}")
        with psycopg.connect(self.dsn, connect_timeout=5, options="-c statement_timeout=5000") as conn:
            conn.execute(
                """INSERT INTO lesson_audit(event_id,job_id,event_type,details)
                VALUES (%s,%s,'learner_interrupt',%s) ON CONFLICT(event_id) DO NOTHING""",
                (event_id, job["job_id"], Jsonb({"session_id": session_id, "request_id": request_id, **details})),
            )

    def chunk_ended(self, session, chunk_id):
        # IDs only: no cognitive-load measurement, decision, text or credentials.
        with psycopg.connect(self.dsn, connect_timeout=5, options="-c statement_timeout=5000") as conn:
            conn.execute(
                """INSERT INTO lesson_audit(event_id,job_id,event_type,details)
                VALUES (%s,%s,'chunk_ended',%s)""",
                (uuid4(), session.binding.lesson_job_id,
                 Jsonb({"session_id": str(session.response.session_id), "chunk_id": chunk_id})),
            )

    def session_ended(self, ended):
        event_id = uuid5(NAMESPACE_URL, f"session-ended:{ended.start_event_id}")
        details = {"session_id": str(ended.response.session_id), "lesson_id": ended.binding.lesson_id,
                   "course_id": ended.binding.course_id, "activity_id": ended.binding.activity_id,
                   "reason": ended.reason, "ended_at": ended.ended_at.isoformat()}
        with psycopg.connect(self.dsn, connect_timeout=5, options="-c statement_timeout=5000") as conn:
            conn.execute(
                """INSERT INTO lesson_audit(event_id,job_id,event_type,details)
                VALUES (%s,%s,'session_ended',%s) ON CONFLICT(event_id) DO NOTHING""",
                (event_id, ended.binding.lesson_job_id, Jsonb(details)),
            )
