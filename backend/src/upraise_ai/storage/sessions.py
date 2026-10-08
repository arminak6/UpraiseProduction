"""Bounded, process-local learner sessions. Nothing is serialized to disk."""

import logging
import time
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Lock
from uuid import uuid4

import psycopg
from fastapi import HTTPException

from upraise_ai.core.errors import IngestionError
from upraise_ai.schemas.sessions import EndReason, StartedSession

INTERRUPT_CACHE_BYTES = 32 * 1024**2


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
    interrupt_lock: object = field(default_factory=Lock, repr=False)
    interrupt_results: OrderedDict = field(default_factory=OrderedDict, repr=False)
    interrupt_ids: dict = field(default_factory=dict, repr=False)


@dataclass
class EndedSession:
    """Bounded retry metadata only: no chat, answer, audio or playback state."""

    binding: SessionBinding
    response: StartedSession
    reason: EndReason
    ended_at: datetime
    retain_until: float
    start_event_id: str
    audited: bool = False
    audit_lock: object = field(default_factory=Lock, repr=False)


class SessionStore:
    def __init__(self, capacity=10000, *, monotonic=time.monotonic, now=lambda: datetime.now(UTC),
                 end_audit=None, ended_ttl_seconds=1800):
        self.capacity, self.monotonic, self.now = capacity, monotonic, now
        self.end_audit, self.ended_ttl_seconds = end_audit, ended_ttl_seconds
        self._sessions: dict[str, RuntimeSession] = {}
        self._ended: OrderedDict[str, EndedSession] = OrderedDict()
        self._pending: set[str] = set()
        self._lock = Lock()
        self._closed = False
        self._interrupt_cache = OrderedDict()
        self._interrupt_cache_bytes = 0

    def _expired(self, session):
        return self.monotonic() >= session.deadline or self.now() >= session.response.expires_at

    def _prune(self):
        for key in [key for key, value in self._ended.items()
                    if value.audited and self.monotonic() >= value.retain_until]:
            del self._ended[key]
        for key in [key for key, value in self._sessions.items() if self._expired(value)]:
            self._end_locked(self._sessions[key], "TIMEOUT")

    @staticmethod
    def _clear_runtime(session):
        session.chat_cache.clear()
        session.interrupt_results.clear()
        session.interrupt_ids.clear()
        session.prompt_guard_counters.clear()
        session.cognitive_load_state = None
        session.current_chunk_index = 0
        session.chunk_ids = ()

    def _end_locked(self, session, reason):
        key = str(session.response.session_id)
        # Active sessions plus unaudited endings share the admission budget.
        # Therefore a pending audit is never evicted to make room for an ending.
        if len(self._ended) >= self.capacity:
            oldest = next(key for key, value in self._ended.items() if value.audited)
            del self._ended[oldest]
        self._discard_results(key)
        self._clear_runtime(session)
        del self._sessions[key]
        ended = EndedSession(session.binding, session.response, reason, self.now(),
                             self.monotonic() + self.ended_ttl_seconds, session.audit_event_id,
                             audited=self.end_audit is None)
        self._ended[key] = ended
        return ended

    def _audit_end(self, ended, audit=None):
        # Only this session's audit is serialized; the global lock stays free.
        with ended.audit_lock:
            if ended.audited:
                return
            try:
                callback = audit or self.end_audit
                if callback is not None:
                    callback(ended)
            except psycopg.Error:
                raise IngestionError("AUDIT_STORAGE_UNAVAILABLE", status=503, retryable=True) from None
            with self._lock:
                ended.audited = True

    @staticmethod
    def _ended_error(ended):
        return HTTPException(410, "SESSION_EXPIRED" if ended.reason == "TIMEOUT" else "SESSION_ENDED")

    def _discard_results(self, session_id):
        session = self._sessions.get(session_id)
        for request_id in session.interrupt_results if session else ():
            self._interrupt_cache_bytes -= self._interrupt_cache.pop((session_id, request_id), 0)

    def prune(self):
        with self._lock:
            self._prune()
            pending = [value for value in self._ended.values() if not value.audited][:64]
        for ended in pending:
            try:
                self._audit_end(ended)
            except IngestionError:
                logging.getLogger(__name__).warning("Session end audit unavailable; retry metadata retained")
                break

    def clear(self):
        with self._lock:
            self._closed = True
            for session in self._sessions.values():
                self._clear_runtime(session)
            self._sessions.clear()
            self._ended.clear()
            self._interrupt_cache.clear()
            self._interrupt_cache_bytes = 0

    def start(self, session, audit):
        key = str(session.response.session_id)
        with self._lock:
            if self._closed:
                raise HTTPException(503, "SESSION_STORAGE_UNAVAILABLE")
            self._prune()
            ended = self._ended.get(key)
            if ended:
                if ended.binding.client_id != session.binding.client_id:
                    raise HTTPException(404, "SESSION_NOT_FOUND")
                raise self._ended_error(ended)
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
            pending_ends = sum(not value.audited for value in self._ended.values())
            if len(self._sessions) + len(self._pending) + pending_ends >= self.capacity:
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

    def get(self, session_id, client_id, *, include_ended=False):
        with self._lock:
            key = str(session_id)
            session = self._sessions.get(key) or self._ended.get(key)
            if not session or session.binding.client_id != client_id:
                raise HTTPException(404, "SESSION_NOT_FOUND")
            if isinstance(session, EndedSession):
                if session.audited and self.monotonic() >= session.retain_until:
                    del self._ended[key]
                    raise HTTPException(404, "SESSION_NOT_FOUND")
                if include_ended:
                    return session
                raise self._ended_error(session)
            if self._expired(session):
                ended = self._end_locked(session, "TIMEOUT")
                if include_ended:
                    return ended
                raise self._ended_error(ended)
            return session

    def end(self, session_id, client_id, reason, *, audit=None):
        if audit is not None:
            with self._lock:
                if self.end_audit is None:
                    self.end_audit = audit
        session = self.get(session_id, client_id, include_ended=True)
        with self._lock:
            key = str(session_id)
            ended = self._ended.get(key)
            if ended is None:
                if self._sessions.get(key) is not session:
                    raise HTTPException(404, "SESSION_NOT_FOUND")
                ended = self._end_locked(session, "TIMEOUT" if self._expired(session) else reason)
        # A failed audit leaves the session closed and content-free. The same
        # request or the background sweep can retry it without a duplicate event.
        self._audit_end(ended, audit)

    @contextmanager
    def interrupt(self, session_id, client_id, request_id, fingerprint):
        """Serialize each learner, never hold the global lock during providers.

        Retain only two inline-audio results and 100 content hashes per session.
        An evicted request ID cannot silently execute again.
        """
        session = self.get(session_id, client_id)
        if not session.interrupt_lock.acquire(blocking=False):
            raise HTTPException(409, "INTERRUPT_IN_PROGRESS", headers={"Retry-After": "2"})
        try:
            with self._lock:
                self._ensure_current_locked(session)
                previous = session.interrupt_ids.get(request_id)
                if previous is not None:
                    if previous != fingerprint:
                        raise HTTPException(409, "INTERRUPT_REQUEST_CONFLICT")
                    if request_id not in session.interrupt_results:
                        raise HTTPException(409, "INTERRUPT_RETRY_EXPIRED")
                    cached = session.interrupt_results[request_id].model_copy(deep=True)
                else:
                    if len(session.interrupt_ids) >= 100:
                        raise HTTPException(429, "SESSION_INTERRUPT_LIMIT_REACHED")
                    cached = None
            yield session, cached
        finally:
            session.interrupt_lock.release()

    def _ensure_current_locked(self, session):
        key = str(session.response.session_id)
        if self._closed or self._sessions.get(key) is not session:
            ended = self._ended.get(key)
            raise self._ended_error(ended) if ended else HTTPException(410, "SESSION_EXPIRED")
        if self._expired(session):
            raise self._ended_error(self._end_locked(session, "TIMEOUT"))

    def ensure_current(self, session):
        with self._lock:
            self._ensure_current_locked(session)

    def finish_interrupt(self, session, request_id, fingerprint, response, message):
        # Caller owns interrupt_lock. This is RAM only; content never enters audit.
        size = len(response.model_dump_json().encode())
        with self._lock:
            self._ensure_current_locked(session)
            session.interrupt_ids[request_id] = fingerprint
            session.interrupt_results[request_id] = response.model_copy(deep=True)
            session_id = str(session.response.session_id)
            self._interrupt_cache[(session_id, request_id)] = size
            self._interrupt_cache_bytes += size
            while len(session.interrupt_results) > 2:
                old_id, _ = session.interrupt_results.popitem(last=False)
                self._interrupt_cache_bytes -= self._interrupt_cache.pop((session_id, old_id))
            # Global 32 MiB quota bounds inline-audio retries across all learners.
            while self._interrupt_cache_bytes > INTERRUPT_CACHE_BYTES:
                (old_session_id, old_id), old_size = self._interrupt_cache.popitem(last=False)
                self._interrupt_cache_bytes -= old_size
                old_session = self._sessions.get(old_session_id)
                if old_session:
                    old_session.interrupt_results.pop(old_id, None)
            if response.text and "INPUT_BLOCKED" not in response.warnings:
                session.chat_cache.extend([{"role": "student", "text": message[:2000]},
                                           {"role": "teacher", "text": response.text[:2000]}])
                session.chat_cache[:] = session.chat_cache[-12:]
            session.current_chunk_index = session.chunk_ids.index(str(response.chunk_id))
            session.prompt_guard_counters["prompts"] += 1
            if "INPUT_BLOCKED" in response.warnings:
                session.prompt_guard_counters["rejections"] += 1
