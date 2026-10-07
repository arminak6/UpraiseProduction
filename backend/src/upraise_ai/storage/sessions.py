"""Bounded, process-local learner sessions. Nothing is serialized to disk."""

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Lock
from uuid import uuid4

from fastapi import HTTPException

from upraise_ai.schemas.sessions import StartedSession


@dataclass(frozen=True)
class SessionBinding:
    client_id: str
    lesson_job_id: str
    result_sha256: str
    course_id: str
    activity_id: str
    lesson_id: str
    document_ids: tuple[str, ...]
    language: str
    learner_level: str
    consent_scope: tuple[str, ...]
    preview: bool
    subject: str | None = None


@dataclass
class RuntimeSession:
    binding: SessionBinding
    response: StartedSession
    deadline: float
    chunk_ids: tuple[str, ...]
    audit_event_id: str = field(default_factory=lambda: str(uuid4()))
    chat_cache: list = field(default_factory=list)
    current_chunk_index: int = 0
    cognitive_load_state: object = None
    prompt_guard_counters: dict = field(default_factory=lambda: {"rejections": 0, "prompts": 0})


class SessionStore:
    def __init__(self, capacity=10000, *, monotonic=time.monotonic, now=lambda: datetime.now(UTC)):
        self.capacity, self.monotonic, self.now = capacity, monotonic, now
        self._sessions: dict[str, RuntimeSession] = {}
        self._pending: set[str] = set()
        self._lock = Lock()
        self._closed = False

    def _expired(self, session):
        return self.monotonic() >= session.deadline or self.now() >= session.response.expires_at

    def _prune(self):
        for key in [key for key, value in self._sessions.items() if self._expired(value)]:
            del self._sessions[key]

    def prune(self):
        with self._lock:
            self._prune()

    def clear(self):
        with self._lock:
            self._closed = True
            self._sessions.clear()

    def start(self, session, audit):
        key = str(session.response.session_id)
        with self._lock:
            if self._closed:
                raise HTTPException(503, "SESSION_STORAGE_UNAVAILABLE")
            self._prune()
            previous = self._sessions.get(key)
            if previous:
                if previous.binding.client_id != session.binding.client_id:
                    raise HTTPException(404, "SESSION_NOT_FOUND")
                if previous.binding != session.binding:
                    raise HTTPException(409, "SESSION_CONTEXT_CONFLICT")
                # A renewed assertion or lesson policy may shorten access. A
                # retry can never extend the original session's lifetime.
                previous.response.expires_at = min(previous.response.expires_at, session.response.expires_at)
                previous.deadline = min(previous.deadline, session.deadline)
                return previous.response.model_copy(deep=True), False
            if key in self._pending:
                raise HTTPException(409, "SESSION_START_IN_PROGRESS", headers={"Retry-After": "2"})
            if len(self._sessions) + len(self._pending) >= self.capacity:
                raise HTTPException(503, "SESSION_CAPACITY_REACHED", headers={"Retry-After": "30"})
            self._pending.add(key)
        try:
            # Audit outside the global lock: unrelated learners can start together.
            audit(session)
            with self._lock:
                if self._closed:
                    raise HTTPException(503, "SESSION_STORAGE_UNAVAILABLE")
                if self._expired(session):
                    raise HTTPException(410, "SESSION_EXPIRED")
                self._sessions[key] = session
            return session.response.model_copy(deep=True), True
        finally:
            with self._lock:
                self._pending.discard(key)

    def get(self, session_id, client_id):
        with self._lock:
            session = self._sessions.get(str(session_id))
            if not session or session.binding.client_id != client_id:
                raise HTTPException(404, "SESSION_NOT_FOUND")
            if self._expired(session):
                del self._sessions[str(session_id)]
                raise HTTPException(410, "SESSION_EXPIRED")
            return session
