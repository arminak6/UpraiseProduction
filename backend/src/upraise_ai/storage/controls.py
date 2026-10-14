"""C3 state and its durable audit outbox commit together in the operational database."""

from uuid import uuid4

import psycopg
from fastapi import HTTPException
from psycopg.types.json import Jsonb

from upraise_ai.storage.operations import OperationsRepository

SCHEMA = """
CREATE TABLE IF NOT EXISTS operational_controls (
    scope_key TEXT NOT NULL CHECK (length(scope_key)<=120),
    feature TEXT NOT NULL CHECK (feature IN ('answers','stt','tts','generation','adaptive_levels','all')),
    PRIMARY KEY(scope_key,feature)
);
CREATE TABLE IF NOT EXISTS operational_control_events (
    event_id UUID PRIMARY KEY,
    actor_client_id TEXT NOT NULL,
    details JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    projected_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS operational_control_events_pending
    ON operational_control_events(created_at) WHERE projected_at IS NULL;
CREATE INDEX IF NOT EXISTS operational_control_events_actor
    ON operational_control_events(actor_client_id,created_at);
"""
CONTROL_VERSION = "runtime-kill-switch-v1"


class ControlsRepository(OperationsRepository):
    def read(self):
        with self.connect() as conn:
            return self._read(conn)

    @staticmethod
    def _read(conn):
        return conn.execute("SELECT scope_key,feature FROM operational_controls ORDER BY scope_key,feature").fetchall()

    def update(self, data, actor, versions):
        scope_key = data.course_id or ""
        with self.connect() as conn:
            # Serialize state changes and the shared 30 successful writes/minute/caller limit.
            conn.execute("SELECT pg_advisory_xact_lock(7824032)")
            count = conn.execute("""SELECT count(*) AS total FROM operational_control_events
                WHERE actor_client_id=%s AND created_at>clock_timestamp()-interval '1 minute'""", (actor,)).fetchone()["total"]
            if count >= 30:
                raise HTTPException(429, "ADMIN_RATE_LIMITED", headers={"Retry-After": "60"})
            old = conn.execute("SELECT 1 FROM operational_controls WHERE scope_key=%s AND feature=%s",
                               (scope_key, data.feature)).fetchone() is not None
            if data.enabled:
                courses = conn.execute("""SELECT count(DISTINCT scope_key) AS total FROM operational_controls
                    WHERE scope_key<>''""").fetchone()["total"]
                known = conn.execute("SELECT 1 FROM operational_controls WHERE scope_key=%s LIMIT 1", (scope_key,)).fetchone()
                if scope_key and not known and courses >= 1000:
                    raise HTTPException(409, "OPERATIONAL_COURSE_LIMIT_REACHED")
                conn.execute("INSERT INTO operational_controls(scope_key,feature) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                             (scope_key, data.feature))
            else:
                conn.execute("DELETE FROM operational_controls WHERE scope_key=%s AND feature=%s", (scope_key, data.feature))
            details = {**data.model_dump(), "previous_enabled": old, "actor_client_id": actor,
                       "control_version": CONTROL_VERSION, "version_snapshot": versions}
            event = conn.execute("""INSERT INTO operational_control_events(event_id,actor_client_id,details)
                VALUES (%s,%s,%s) RETURNING event_id,details,created_at""", (uuid4(), actor, Jsonb(details))).fetchone()
            rows = self._read(conn)
        return event, rows

    def project(self, event):
        from upraise_ai.storage.audit import AuditRepository

        dsn = self.settings.audit_dsn.get_secret_value()
        if not dsn:
            return False
        try:
            AuditRepository(dsn).admin_control_changed(event)
            with self.connect() as conn:
                conn.execute("UPDATE operational_control_events SET projected_at=clock_timestamp() WHERE event_id=%s",
                             (event["event_id"],))
        except psycopg.Error:
            return False
        return True

    def project_pending(self):
        if not self.dsn:
            return
        with self.connect() as conn:
            events = conn.execute("""SELECT event_id,details,created_at FROM operational_control_events
                WHERE projected_at IS NULL ORDER BY created_at LIMIT 100""").fetchall()
        for event in events:
            if not self.project(event):
                break
