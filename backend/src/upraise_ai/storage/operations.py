"""Bounded provider observations and worker heartbeats; no request or learner data."""

import hashlib
import json
import os
import time
from threading import Lock

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

SCHEMA = """
CREATE TABLE IF NOT EXISTS operational_observations (
    configuration_id TEXT NOT NULL,
    component TEXT NOT NULL CHECK (component IN (
        'bedrock_generation','bedrock_review','bedrock_embeddings','polly','transcribe','lesson_worker')),
    state TEXT NOT NULL CHECK (state IN ('AVAILABLE','UNAVAILABLE')),
    reason_code TEXT NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY(configuration_id,component)
);
"""


def configuration_id(settings, *, credential_digest=None):
    # Credential rotation/model changes must not reuse an old provider result.
    credentials = credential_digest
    if credentials is None:
        try:
            credentials = hashlib.sha256(settings.aws_credentials_file.read_bytes()).hexdigest()
        except OSError:
            credentials = "ambient"
    payload = [settings.aws_region, settings.lesson_generation_model_id, settings.lesson_review_model_id,
               settings.embedding_model_id, settings.polly_engine, credentials]
    return hashlib.sha256(json.dumps(payload).encode()).hexdigest()


class OperationsRepository:
    def __init__(self, settings):
        self.settings = settings
        self.dsn = settings.jobs_dsn.get_secret_value()

    def connect(self):
        options = conninfo_to_dict(self.dsn).get("options", "")
        return psycopg.connect(self.dsn, connect_timeout=1, row_factory=dict_row,
                              options=options + " -c statement_timeout=1000 -c lock_timeout=500")

    def report(self, components, state, reason, *, configuration=None):
        identity = configuration or configuration_id(self.settings)
        with self.connect() as conn:
            # Keep at most the current/recent configurations; no request history.
            conn.execute("DELETE FROM operational_observations WHERE observed_at<clock_timestamp()-interval '1 day'")
            for component in components:
                conn.execute(
                    """INSERT INTO operational_observations(configuration_id,component,state,reason_code)
                    VALUES (%s,%s,%s,%s) ON CONFLICT(configuration_id,component) DO UPDATE
                    SET state=excluded.state,reason_code=excluded.reason_code,observed_at=clock_timestamp()""",
                    (identity, component, state, reason),
                )

    def read(self):
        with self.connect() as conn:
            return conn.execute(
                """SELECT component,state,reason_code,observed_at,
                EXTRACT(EPOCH FROM clock_timestamp()-observed_at) AS age_seconds
                FROM operational_observations WHERE configuration_id=%s""", (configuration_id(self.settings),),
            ).fetchall()


_recent = {}
_lock = Lock()


def observe(settings, components, state, reason, *, interval=10, configuration=None):
    """Best effort, coalesced, bounded writes must never change provider results."""
    if not settings.jobs_dsn.get_secret_value() or not components:
        return
    identity = configuration or configuration_id(settings)
    key = (os.getpid(), identity, tuple(components))
    now = time.monotonic()
    with _lock:
        previous = _recent.get(key)
        if previous and previous[0] == (state, reason) and now-previous[1] < interval:
            return
        if len(_recent) >= 64:
            _recent.clear()
        _recent[key] = ((state, reason), now)
    try:
        OperationsRepository(settings).report(components, state, reason, configuration=identity)
    except Exception:
        # No exception text: it can contain connection strings or provider data.
        pass


def lesson_worker_heartbeat(settings):
    from pathlib import Path

    Path("/tmp/upraise-lesson-heartbeat").touch()
    observe(settings, ["lesson_worker"], "AVAILABLE", "WORKER_HEARTBEAT")
