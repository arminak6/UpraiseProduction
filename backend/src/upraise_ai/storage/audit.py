from uuid import NAMESPACE_URL, uuid4, uuid5

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.types.json import Jsonb

from upraise_ai.services.versions import VersionCatalog

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
CREATE TABLE IF NOT EXISTS admin_audit (
    event_id UUID PRIMARY KEY, event_type TEXT NOT NULL, details JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
"""


class AuditRepository:
    def __init__(self, dsn, *, settings=None):
        self.dsn = dsn
        self.version_snapshot = VersionCatalog(settings).snapshot().model_dump(mode="json") if settings else None

    def details(self, details):
        # Snapshot the component writing the event. A read/end event does not
        # claim these versions generated the older lesson it references.
        if self.version_snapshot is None:
            return details
        return {**details, "version_snapshot": self.version_snapshot}

    def migrate(self):
        with psycopg.connect(self.dsn, connect_timeout=5) as conn:
            conn.execute(SCHEMA)

    def admin_control_changed(self, event):
        options = conninfo_to_dict(self.dsn).get("options", "") + " -c statement_timeout=1000"
        with psycopg.connect(self.dsn, connect_timeout=1, options=options) as conn:
            conn.execute("""INSERT INTO admin_audit(event_id,event_type,details,created_at)
                VALUES (%s,'KILL_SWITCH_UPDATED',%s,%s) ON CONFLICT(event_id) DO NOTHING""",
                         (event["event_id"], Jsonb(event["details"]), event["created_at"]))

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
                (event_id, job["job_id"], job["document_id"], event_type, Jsonb(self.details(details))),
            )

    def lesson(self, job, event_type, details, *, attempt=False):
        identity = f"lesson:{job['job_id']}:{event_type}"
        if attempt:
            identity += f":{job['lease_token']}"
        with psycopg.connect(self.dsn, connect_timeout=5) as conn:
            conn.execute(
                """INSERT INTO lesson_audit(event_id,job_id,event_type,details) VALUES (%s,%s,%s,%s)
                ON CONFLICT(event_id) DO NOTHING""",
                (uuid5(NAMESPACE_URL, identity), job["job_id"], event_type, Jsonb(self.details(details))),
            )

    def lesson_access(self, job, event_type, details):
        # Access is per request, unlike idempotent lifecycle events. Only artifact
        # IDs/hashes/counts are recorded; no lesson content or credentials.
        with psycopg.connect(self.dsn, connect_timeout=5) as conn:
            conn.execute(
                """INSERT INTO lesson_audit(event_id,job_id,event_type,details)
                VALUES (%s,%s,%s,%s)""",
                (uuid4(), job["job_id"], event_type, Jsonb(self.details(details))),
            )

    def session_started(self, job, event_id, details):
        # Pseudonymous session UUID + content-free version metadata only. No JWT,
        # learner identity, chat, telemetry or lesson text is persisted.
        with psycopg.connect(self.dsn, connect_timeout=5, options="-c statement_timeout=5000") as conn:
            conn.execute(
                """INSERT INTO lesson_audit(event_id,job_id,event_type,details)
                VALUES (%s,%s,'session_started',%s) ON CONFLICT(event_id) DO NOTHING""",
                (event_id, job["job_id"], Jsonb(self.details(details))),
            )

    def generated_content_deleted(self, job, operation):
        identity = f"generated-content-deleted:{operation['deletion_id']}"
        details = {"deletion_id": str(operation["deletion_id"]), "course_id": job["metadata"]["course_id"],
                   "learner_level": None if operation["level_key"] == "*" else operation["level_key"],
                   "levels": operation["levels"]}
        with psycopg.connect(self.dsn, connect_timeout=5) as conn:
            conn.execute("""INSERT INTO lesson_audit(event_id,job_id,event_type,details)
                VALUES (%s,%s,'GENERATED_CONTENT_DELETED',%s) ON CONFLICT(event_id) DO NOTHING""",
                (uuid5(NAMESPACE_URL, identity), job["job_id"], Jsonb(self.details(details))))

    def learner_interrupt(self, job, session_id, request_id, details):
        event_id = uuid5(NAMESPACE_URL, f"interrupt:{session_id}:{request_id}")
        with psycopg.connect(self.dsn, connect_timeout=5, options="-c statement_timeout=5000") as conn:
            conn.execute(
                """INSERT INTO lesson_audit(event_id,job_id,event_type,details)
                VALUES (%s,%s,'learner_interrupt',%s) ON CONFLICT(event_id) DO NOTHING""",
                (event_id, job["job_id"], Jsonb(self.details({"session_id": session_id, "request_id": request_id, **details}))),
            )

    def chunk_ended(self, session, chunk_id):
        # IDs and deployed versions only: no score, decision, text or credentials.
        with psycopg.connect(self.dsn, connect_timeout=5, options="-c statement_timeout=5000") as conn:
            conn.execute(
                """INSERT INTO lesson_audit(event_id,job_id,event_type,details)
                VALUES (%s,%s,'chunk_ended',%s)""",
                (uuid4(), session.binding.lesson_job_id,
                 Jsonb(self.details({"session_id": str(session.response.session_id), "chunk_id": chunk_id}))),
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
                (event_id, ended.binding.lesson_job_id, Jsonb(self.details(details))),
            )
