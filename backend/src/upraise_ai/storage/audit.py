from uuid import NAMESPACE_URL, uuid5

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
