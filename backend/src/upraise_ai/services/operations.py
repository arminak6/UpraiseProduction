"""C1/C2 cached checks; polling performs no inference, synthesis or transcription."""

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Lock

import httpx
import psycopg
from psycopg.conninfo import conninfo_to_dict

from upraise_ai.guards.operations import control_state, feature_enabled, kill_switch_active
from upraise_ai.schemas.operations import DependencyStatus, FeatureStatus, ReadinessStatus, ServiceStatus
from upraise_ai.storage.controls import ControlsRepository
from upraise_ai.storage.operations import OperationsRepository

CORE = ("postgres_jobs", "postgres_audit", "qdrant", "artifacts")
PROVIDERS = ("bedrock_generation", "bedrock_review", "bedrock_embeddings", "polly", "transcribe")
FEATURE_DEPENDENCIES = {
    "answers": (*CORE, "qdrant_collection", "bedrock_generation", "bedrock_review", "bedrock_embeddings"),
    "stt": ("transcribe",),
    # Speech preparation also uses the teaching/review models for math narration.
    "tts": ("polly", "bedrock_generation", "bedrock_review"),
    "generation": (*CORE, "qdrant_collection", "bedrock_generation", "bedrock_review", "polly", "lesson_worker"),
    "adaptive_levels": ("postgres_jobs", "postgres_audit"),
}


def dependency(state, reason, observed_at=None):
    return DependencyStatus(state=state, reason_code=reason, observed_at=observed_at)


class OperationsMonitor:
    def __init__(self, settings, *, probes=None, repository=None, clock=time.monotonic):
        self.settings, self.clock = settings, clock
        self.repository = repository or OperationsRepository(settings)
        self.probes = probes or {"postgres_jobs": self.jobs, "postgres_audit": self.audit,
                                 "qdrant": self.qdrant, "artifacts": self.artifacts}
        self.executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="upraise-readiness")
        self.lock = Lock()
        self.current = ReadinessStatus(status="NOT_READY", checked_at=None, dependencies={
            key: dependency("UNKNOWN", "CHECK_PENDING") for key in (*CORE, "qdrant_collection", *PROVIDERS, "lesson_worker")})
        self.refreshed = None

    def close(self):
        self.executor.shutdown(wait=False, cancel_futures=True)

    def database(self, dsn, tables):
        if not dsn:
            return dependency("UNAVAILABLE", "STORAGE_NOT_CONFIGURED")
        try:
            with psycopg.connect(dsn, connect_timeout=self.settings.operations_probe_timeout_seconds,
                                options=conninfo_to_dict(dsn).get("options", "") +
                                " -c statement_timeout=1500 -c lock_timeout=500") as conn:
                for table in tables:
                    if conn.execute("SELECT to_regclass(%s)", (table,)).fetchone()[0] is None:
                        return dependency("UNAVAILABLE", "MIGRATION_REQUIRED")
                    if not all(conn.execute("SELECT has_table_privilege(current_user,%s,%s)",
                                            (table, permission)).fetchone()[0]
                               for permission in ("SELECT", "INSERT", "UPDATE")):
                        return dependency("UNAVAILABLE", "STORAGE_PERMISSION_DENIED")
            return dependency("AVAILABLE", "STORAGE_REACHABLE")
        except Exception:
            return dependency("UNAVAILABLE", "STORAGE_UNAVAILABLE")

    def jobs(self):
        return self.database(self.settings.jobs_dsn.get_secret_value(),
                             ("ingestion_jobs", "lesson_jobs", "prepared_lesson_chunks", "operational_observations",
                              "operational_controls", "operational_control_events"))

    def audit(self):
        # Audit is append-only; it deliberately does not need UPDATE privileges.
        dsn = self.settings.audit_dsn.get_secret_value()
        if not dsn:
            return dependency("UNAVAILABLE", "STORAGE_NOT_CONFIGURED")
        try:
            with psycopg.connect(dsn, connect_timeout=self.settings.operations_probe_timeout_seconds,
                                options=conninfo_to_dict(dsn).get("options", "") + " -c statement_timeout=1500") as conn:
                for table in ("ingestion_audit", "lesson_audit", "admin_audit"):
                    if conn.execute("SELECT to_regclass(%s)", (table,)).fetchone()[0] is None:
                        return dependency("UNAVAILABLE", "MIGRATION_REQUIRED")
                    if not conn.execute("SELECT has_table_privilege(current_user,%s,'INSERT')", (table,)).fetchone()[0]:
                        return dependency("UNAVAILABLE", "AUDIT_PERMISSION_DENIED")
            return dependency("AVAILABLE", "AUDIT_REACHABLE")
        except Exception:
            return dependency("UNAVAILABLE", "AUDIT_UNAVAILABLE")

    def qdrant(self):
        try:
            with httpx.Client(timeout=self.settings.operations_probe_timeout_seconds, trust_env=False) as client:
                client.get(f"{self.settings.qdrant_url.rstrip('/')}/readyz").raise_for_status()
                result = client.get(f"{self.settings.qdrant_url.rstrip('/')}/collections/{self.settings.qdrant_collection}")
                if result.status_code == 404:
                    return dependency("AVAILABLE", "QDRANT_COLLECTION_PENDING")
                result.raise_for_status()
                vectors = result.json()["result"]["config"]["params"]["vectors"]
                if vectors.get("size") != self.settings.embedding_dimension or vectors.get("distance") != "Cosine":
                    return dependency("AVAILABLE", "QDRANT_COLLECTION_MISMATCH")
            return dependency("AVAILABLE", "QDRANT_COLLECTION_REACHABLE")
        except Exception:
            return dependency("UNAVAILABLE", "QDRANT_UNAVAILABLE")

    def artifacts(self):
        root = self.settings.data_dir
        if root.is_dir() and os.access(root, os.R_OK | os.W_OK | os.X_OK):
            return dependency("AVAILABLE", "ARTIFACT_DIRECTORY_ACCESSIBLE")
        return dependency("UNAVAILABLE", "ARTIFACT_DIRECTORY_UNAVAILABLE")

    def credentials(self):
        path = self.settings.aws_credentials_file
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if not all(isinstance(data.get(key), str) and data[key].strip()
                           for key in ("aws_access_key_id", "aws_secret_access_key")):
                    raise ValueError("Invalid configuration")
                return dependency("AVAILABLE", "AWS_CREDENTIALS_CONFIGURED")
            except Exception:
                return dependency("UNAVAILABLE", "AWS_CREDENTIAL_FILE_INVALID")
        # Do not invoke metadata/STS APIs; ambient IAM credentials can still work.
        return dependency("UNKNOWN", "AWS_CREDENTIALS_NOT_VERIFIED")

    def refresh(self):
        try:
            ControlsRepository(self.settings).project_pending()
        except Exception:
            # The durable outbox is retried on the next refresh; never expose database errors.
            pass
        futures = {key: self.executor.submit(probe) for key, probe in self.probes.items()}
        states = {}
        for key, future in futures.items():
            try:
                states[key] = future.result(timeout=self.settings.operations_probe_timeout_seconds + 3)
            except Exception:
                states[key] = dependency("UNKNOWN", "CHECK_FAILED")
        for key in (*PROVIDERS, "lesson_worker"):
            states[key] = dependency("UNKNOWN", "PROVIDER_NOT_VERIFIED" if key in PROVIDERS else "WORKER_NOT_SEEN")
        qdrant = states["qdrant"]
        states["qdrant_collection"] = (dependency("UNAVAILABLE", qdrant.reason_code)
            if qdrant.reason_code in {"QDRANT_COLLECTION_PENDING", "QDRANT_COLLECTION_MISMATCH"}
            else qdrant.model_copy())
        if states["postgres_jobs"].state == "AVAILABLE":
            try:
                for row in self.repository.read():
                    key = row["component"]
                    if key not in (*PROVIDERS, "lesson_worker"):
                        continue
                    ttl = (self.settings.operations_worker_ttl_seconds if key == "lesson_worker"
                           else self.settings.operations_provider_ttl_seconds)
                    if row["reason_code"] in {"PROVIDER_SERVICE_ERROR", "PROVIDER_THROTTLED",
                                               "PROVIDER_CONNECTION_FAILED", "STT_PROVIDER_UNAVAILABLE"}:
                        ttl = min(ttl, 30)
                    if 0 <= row["age_seconds"] <= ttl:
                        states[key] = dependency(row["state"], row["reason_code"], row["observed_at"])
                    elif key == "lesson_worker":
                        states[key] = dependency("UNAVAILABLE", "WORKER_HEARTBEAT_EXPIRED", row["observed_at"])
                    else:
                        states[key] = dependency("UNKNOWN", "PROVIDER_OBSERVATION_EXPIRED", row["observed_at"])
            except Exception:
                states["postgres_jobs"] = dependency("UNAVAILABLE", "OPERATIONS_STORAGE_UNAVAILABLE")
        credentials = self.credentials()
        if credentials.state == "UNAVAILABLE":
            for key in PROVIDERS:
                states[key] = credentials.model_copy()
        status = ("NOT_READY" if any(states[key].state != "AVAILABLE" for key in CORE) else
                  "DEGRADED" if any(states[key].state != "AVAILABLE" for key in (*PROVIDERS, "qdrant_collection", "lesson_worker"))
                  else "READY")
        snapshot = ReadinessStatus(status=status, checked_at=datetime.now(UTC), dependencies=states)
        with self.lock:
            self.current, self.refreshed = snapshot, self.clock()

    def snapshot(self):
        with self.lock:
            result = self.current.model_copy(deep=True)
            stale = self.refreshed is None or self.clock()-self.refreshed > self.settings.operations_refresh_seconds*3
        if stale:
            result.status = "NOT_READY"
            result.dependencies = {key: dependency("UNKNOWN", "MONITOR_STALE", value.observed_at)
                                   for key, value in result.dependencies.items()}
        return result

    def status(self, course_id=None):
        snapshot = self.snapshot()
        controls = control_state(self.settings)
        features = {}
        for feature, keys in FEATURE_DEPENDENCIES.items():
            enabled = feature_enabled(self.settings, feature, course_id, state=controls)
            reasons = []
            if not enabled:
                state = "UNAVAILABLE"
                reasons = ["AI_DISABLED" if controls.killed(course_id) else f"{feature.upper()}_DISABLED"]
            else:
                checks = [snapshot.dependencies[key] for key in keys]
                state = ("UNAVAILABLE" if any(value.state == "UNAVAILABLE" for value in checks) else
                         "UNKNOWN" if any(value.state == "UNKNOWN" for value in checks) else "AVAILABLE")
                reasons = list(dict.fromkeys(value.reason_code for value in checks if value.state != "AVAILABLE"))
            features[feature] = FeatureStatus(enabled=enabled, state=state,
                available=None if state == "UNKNOWN" else state == "AVAILABLE", reason_codes=reasons)
        # Audio interruptions and audible answers need a functioning answer pipeline too.
        if not feature_enabled(self.settings, "tts", course_id, state=controls):
            features["generation"].state, features["generation"].available = "UNAVAILABLE", False
            features["generation"].reason_codes = list(dict.fromkeys([
                *features["generation"].reason_codes, "AI_DISABLED" if controls.killed(course_id)
                else "TTS_DISABLED"]))
        for feature in ("stt", "tts"):
            answer = features["answers"]
            if features[feature].enabled and answer.state != "AVAILABLE":
                current = features[feature]
                if current.state != "UNAVAILABLE":
                    current.state, current.available = answer.state, answer.available
                current.reason_codes = list(dict.fromkeys([*current.reason_codes, *answer.reason_codes]))
        return ServiceStatus(course_id=course_id, checked_at=snapshot.checked_at,
            answers_available=features["answers"].available, stt_available=features["stt"].available,
            tts_available=features["tts"].available, generation_available=features["generation"].available,
            kill_switch_active=kill_switch_active(self.settings, course_id, state=controls), features=features)
