"""B1 resolves an available A3 generation, then opens ephemeral runtime state."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import psycopg
from fastapi import HTTPException

from upraise_ai.core.errors import IngestionError
from upraise_ai.guards.session_jwt import validate_session_jwt
from upraise_ai.schemas.aligned_lessons import AlignedLessonResult
from upraise_ai.schemas.chunk_end import ChunkEndResponse
from upraise_ai.schemas.sessions import StartedSession
from upraise_ai.services.lesson_replacement import artifact_job
from upraise_ai.storage.sessions import RuntimeSession, SessionBinding

AI_NOTICES = {
    "en": "You are interacting with an AI teaching assistant. Its responses may contain mistakes.",
    "it": "Stai interagendo con un assistente didattico basato sull'intelligenza artificiale. Le sue risposte possono contenere errori.",
    "fr": "Vous interagissez avec un assistant pédagogique fondé sur l'intelligence artificielle. Ses réponses peuvent contenir des erreurs.",
    "de": "Sie interagieren mit einem KI-Lernassistenten. Seine Antworten können Fehler enthalten.",
    "es": "Estás interactuando con un asistente educativo de inteligencia artificial. Sus respuestas pueden contener errores.",
}


def authorize_session(settings, store, session_id, principal, token=None, *, include_ended=False, lessons=None):
    """Shared B2/B4/B5 authorization; feature consent is enforced separately."""
    principal.require_scope("sessions:write")
    session = store.get(session_id, principal.client_id, include_ended=include_ended)
    binding = session.binding
    principal.require_course(binding.course_id)
    local = principal.local_test and settings.environment == "development" and settings.local_test_mode
    if not (local and token is None and binding.preview):
        claims = validate_session_jwt(token, settings)
        expected = (str(session.response.session_id), binding.client_id, binding.subject, binding.lesson_job_id,
                    binding.course_id, binding.activity_id, binding.lesson_id, binding.result_sha256,
                    binding.consent_scope)
        actual = (str(claims.session_id), claims.client_id, claims.sub, str(claims.lesson_job_id),
                  claims.course_id, claims.activity_id, claims.lesson_id, claims.result_sha256,
                  tuple(sorted(claims.consent_scope)))
        if (actual != expected or not claims.lesson_approved
                or (binding.level_claim_required and claims.learner_level != binding.learner_level)):
            raise HTTPException(403, "SESSION_SCOPE_MISMATCH")
    if lessons is not None and not include_ended:
        row = lessons.authorized(binding.lesson_job_id, principal)
        if (row.get("result") or {}).get("result_sha256") != binding.result_sha256:
            store.end(session_id, principal.client_id, "ERROR")
            raise HTTPException(409, "LESSON_VERSION_MISMATCH")
    return session


class SessionService:
    def __init__(self, settings, lessons, store):
        self.settings, self.lessons, self.store = settings, lessons, store

    def chunk_end(self, session_id, data, principal, token=None):
        session = authorize_session(self.settings, self.store, session_id, principal, token, lessons=self.lessons)
        try:
            decision = self.store.chunk_end(session, data.lesson_id, str(data.chunk_id),
                                            data.cognitive_load_score, self.lessons.audit.chunk_ended)
        except psycopg.Error:
            raise IngestionError("AUDIT_STORAGE_UNAVAILABLE", status=503, retryable=True) from None
        return ChunkEndResponse(decision=decision)

    def end(self, session_id, data, principal, token=None):
        authorize_session(self.settings, self.store, session_id, principal, token, include_ended=True)
        self.store.end(session_id, principal.client_id, data.reason, audit=self.lessons.audit.session_ended)

    def start(self, data, principal, token=None):
        principal.require_scope("sessions:write")
        principal.require_course(data.course_id)
        local = (principal.local_test and self.settings.environment == "development"
                 and self.settings.local_test_mode)
        if local and token is None:
            claims = None
            session_id, consent = data.session_id or uuid4(), tuple(sorted(data.consent_scope or []))
        else:
            if data.session_id is not None or data.consent_scope is not None:
                raise HTTPException(422, "SESSION_CLAIMS_MUST_COME_FROM_JWT")
            claims = validate_session_jwt(token, self.settings)
            if claims.client_id != principal.client_id:
                raise HTTPException(403, "SESSION_CLIENT_MISMATCH")
            if (claims.course_id, claims.activity_id, claims.lesson_id, claims.lesson_job_id) != (
                    data.course_id, data.activity_id, data.lesson_id, data.lesson_job_id):
                raise HTTPException(403, "SESSION_SCOPE_MISMATCH")
            if not claims.lesson_approved:
                raise HTTPException(403, "LESSON_APPROVAL_REQUIRED")
            session_id, consent = claims.session_id, tuple(sorted(claims.consent_scope))

        row = self.lessons.authorized(data.lesson_job_id, principal)
        with self.lessons.staging.artifact_lock(str(data.lesson_job_id), shared=True):
            if row["status"] == "DONE" and row["expires_at"] is None:
                raise IngestionError("LESSON_OUTPUT_UNAVAILABLE", status=503)
            result = self.lessons.result(row)
            level_required = isinstance(result, AlignedLessonResult) or result.generation_version == 'a3-v2'
            requested_level = data.learner_level or (claims.learner_level if claims else None)
            if claims and data.learner_level is not None and claims.learner_level != data.learner_level:
                raise HTTPException(403, 'SESSION_LEVEL_MISMATCH')
            if isinstance(result, AlignedLessonResult):
                if requested_level is None:
                    raise HTTPException(422, 'LESSON_LEVEL_REQUIRED')
                if requested_level not in result.variants:
                    raise HTTPException(404, 'LESSON_LEVEL_NOT_FOUND')
                result = result.variants[requested_level]
            elif requested_level is not None and requested_level != result.learner_level:
                raise HTTPException(409, 'LESSON_LEVEL_MISMATCH')
            if claims and level_required and claims.learner_level != result.learner_level:
                raise HTTPException(403, 'SESSION_LEVEL_MISMATCH')
            if (result.job_id, result.course_id, result.activity_id, result.lesson_id) != (
                    str(data.lesson_job_id), data.course_id, data.activity_id, data.lesson_id):
                raise HTTPException(409, "LESSON_CONTEXT_MISMATCH")
            if data.language is not None and data.language.lower() != result.language.lower():
                raise HTTPException(409, "LESSON_LANGUAGE_MISMATCH")
            source_ids = tuple(row["metadata"]["document_ids"])
            selected = tuple(str(value) for value in data.document_ids) if data.document_ids is not None else source_ids
            if not set(selected).issubset(source_ids):
                raise HTTPException(403, "SESSION_DOCUMENT_SCOPE_DENIED")
            digest = row["result"]["result_sha256"]
            if claims and claims.result_sha256 != digest:
                raise HTTPException(409, "LESSON_VERSION_MISMATCH")
            if not result.chunks:
                raise IngestionError("LESSON_OUTPUT_UNAVAILABLE", status=503)
            try:
                chunk_ids = tuple(str(UUID(chunk.chunk_id)) for chunk in result.chunks)
            except ValueError:
                raise IngestionError("LESSON_OUTPUT_UNAVAILABLE", status=503) from None
            if (len(set(chunk_ids)) != len(chunk_ids)
                    or [chunk.index for chunk in result.chunks] != list(range(len(chunk_ids)))):
                raise IngestionError("LESSON_OUTPUT_UNAVAILABLE", status=503)
            for chunk_id in chunk_ids:
                if not self.lessons.staging.job_dir(artifact_job(row)).joinpath(f"{chunk_id}.wav").is_file():
                    raise IngestionError("LESSON_AUDIO_UNAVAILABLE", status=503)
            now = datetime.now(UTC)
            expires = min(now + timedelta(seconds=self.settings.session_ttl_seconds),
                          datetime.fromisoformat(result.expires_at))
            if claims and claims.exp < expires.timestamp():
                # Convert only a nearer token deadline, avoiding datetime
                # overflow from an otherwise valid distant NumericDate.
                expires = datetime.fromtimestamp(claims.exp, UTC)
            if expires <= now:
                raise HTTPException(410, "SESSION_EXPIRED")
            binding = SessionBinding(
                principal.client_id, str(data.lesson_job_id), digest, data.course_id, data.activity_id,
                data.lesson_id, tuple(sorted(selected)), result.language.lower(), result.learner_level, consent, not bool(claims),
                claims.sub if claims else None, level_required,
                row['metadata'].get('level_profiles', {}).get(result.learner_level) if level_required else None,
            )
            response = StartedSession(
                session_id=session_id, lesson_job_id=data.lesson_job_id, lesson_id=data.lesson_id,
                course_id=data.course_id, activity_id=data.activity_id, document_ids=selected,
                language=result.language, learner_level=result.learner_level, voice=result.voice,
                result_sha256=digest, preview=binding.preview,
                ai_notice=AI_NOTICES.get(result.language.lower().split("-")[0], AI_NOTICES["en"]), expires_at=expires,
            )
            session = RuntimeSession(binding, response, self.store.monotonic() + (expires-now).total_seconds(),
                                     chunk_ids)

            def audit(value):
                try:
                    self.lessons.audit.session_started(row, value.audit_event_id, {
                        "session_id": str(session_id), "result_sha256": digest,
                        "language": result.language, "learner_level": result.learner_level,
                        "document_count": len(selected), "preview": binding.preview,
                        "expires_at": expires.isoformat(),
                    })
                except psycopg.Error:
                    raise IngestionError("AUDIT_STORAGE_UNAVAILABLE", status=503, retryable=True) from None

            # Fence a B1 start that raced deletion: publish RAM state only while
            # the exact job and all its sources are still available.
            with self.lessons.jobs.job_transaction(str(data.lesson_job_id)) as (conn, current):
                if not current or current["cleanup_requested_at"] or current.get("content_deletion_requested_at"):
                    raise IngestionError("LESSON_OUTPUT_EXPIRED", status=410)
                from upraise_ai.services.lesson_pipeline import LessonPipeline
                LessonPipeline(self.settings, self.lessons.documents, None, self.lessons.staging, None, None).check_sources(
                    current, result.source_attempts, conn)
                return self.store.start(session, audit)
