"""B4: request-local LangGraph, exact prepared teaching context, ephemeral results."""

import base64
import hashlib
import json
import re
from collections import defaultdict
from contextlib import ExitStack
from threading import BoundedSemaphore
from typing import TypedDict
from uuid import NAMESPACE_URL, uuid5

import psycopg
from botocore.config import Config
from fastapi import HTTPException
from langgraph.graph import END, START, StateGraph
from langsmith import tracing_context

from upraise_ai.core.errors import IngestionError
from upraise_ai.guards.session_jwt import validate_session_jwt
from upraise_ai.schemas.interrupts import InlineAudio, InterruptResponse, LearnerIntent
from upraise_ai.schemas.lessons import LessonSource
from upraise_ai.services.aws import aws_session
from upraise_ai.services.embeddings import BedrockTitanEmbeddingProvider
from upraise_ai.services.interrupt_models import InterruptTeacher, normalized
from upraise_ai.services.interrupt_stt import InterruptSTT, wav_pcm
from upraise_ai.services.lesson_speech import LessonSpeech


def interrupt_speech(settings, models):
    client = aws_session(settings).client("polly", region_name=settings.aws_region,
        config=Config(connect_timeout=5, read_timeout=settings.interrupt_provider_timeout_seconds,
                      retries={"total_max_attempts": 1, "mode": "standard"}))
    return LessonSpeech(settings, models, client=client)

MESSAGES = {
    "en": ["This is not covered by the course material.", "I can help with your course learning. Please ask a course-related question or request.", "What would you like me to explain or do next?"],
    "it": ["Questo argomento non è trattato nel materiale del corso.", "Posso aiutarti a studiare il corso. Fai una domanda o una richiesta relativa al corso.", "Che cosa vorresti che spiegassi o facessi adesso?"],
    "fr": ["Ce sujet n'est pas couvert par le contenu du cours.", "Je peux vous aider à étudier le cours. Posez une question ou une demande relative au cours.", "Que souhaitez-vous que j'explique ou fasse maintenant ?"],
    "de": ["Dieses Thema wird im Kursmaterial nicht behandelt.", "Ich kann beim Lernen für diesen Kurs helfen. Stelle eine Frage oder Bitte zum Kurs.", "Was soll ich als Nächstes erklären oder tun?"],
    "es": ["Este tema no está cubierto por el material del curso.", "Puedo ayudarte a estudiar el curso. Haz una pregunta o una petición relacionada con el curso.", "¿Qué quieres que explique o haga ahora?"],
}


class TurnState(TypedDict, total=False):
    message: str
    intent: LearnerIntent
    chunks: list
    response: InterruptResponse


class InterruptService:
    def __init__(self, settings, lessons, store, vectors, *, teacher_factory=InterruptTeacher,
                 embeddings=None, stt=None, speech_factory=interrupt_speech):
        self.settings, self.lessons, self.store, self.vectors = settings, lessons, store, vectors
        self.teacher_factory, self.embeddings = teacher_factory, embeddings
        self.stt, self.speech_factory = stt or InterruptSTT(settings), speech_factory
        self.capacity = BoundedSemaphore(settings.interrupt_max_concurrent)
        self.input_capacity = BoundedSemaphore(settings.interrupt_max_concurrent)

    def authorized_session(self, session_id, principal, token):
        principal.require_scope("sessions:write")
        session = self.store.get(session_id, principal.client_id)
        binding = session.binding
        principal.require_course(binding.course_id)
        local = principal.local_test and self.settings.environment == "development" and self.settings.local_test_mode
        if not (local and token is None and binding.preview):
            claims = validate_session_jwt(token, self.settings)
            expected = (str(session.response.session_id), binding.client_id, binding.subject, binding.lesson_job_id,
                        binding.course_id, binding.activity_id, binding.lesson_id, binding.result_sha256,
                        binding.consent_scope)
            actual = (str(claims.session_id), claims.client_id, claims.sub, str(claims.lesson_job_id),
                      claims.course_id, claims.activity_id, claims.lesson_id, claims.result_sha256,
                      tuple(sorted(claims.consent_scope)))
            if actual != expected or not claims.lesson_approved:
                raise HTTPException(403, "SESSION_SCOPE_MISMATCH")
        if "questions" not in binding.consent_scope:
            raise HTTPException(403, "QUESTIONS_CONSENT_REQUIRED")
        return session

    def _lesson(self, session, data, principal):
        row = self.lessons.authorized(session.binding.lesson_job_id, principal)
        with self.lessons.staging.artifact_lock(session.binding.lesson_job_id, shared=True):
            result = self.lessons.result(row)
            if row["result"]["result_sha256"] != session.binding.result_sha256:
                raise HTTPException(409, "LESSON_VERSION_MISMATCH")
            chunk = next((value for value in result.chunks if value.chunk_id == str(data.chunk_id)), None)
            if chunk is None or str(data.chunk_id) not in session.chunk_ids:
                raise HTTPException(404, "LESSON_CHUNK_NOT_FOUND")
            if data.playback_offset_ms is not None and data.playback_offset_ms > chunk.duration_seconds * 1000 + 1000:
                raise HTTPException(422, "INVALID_PLAYBACK_OFFSET")
            prepared = self.lessons.jobs.prepared_chunk(session.binding.lesson_job_id, str(data.chunk_id))
            if not prepared or prepared["text"] != chunk.text or prepared["sources"] != [s.model_dump() for s in chunk.sources]:
                raise IngestionError("LESSON_PREPARED_TEXT_CONFLICT", status=503)
            if len(prepared["text"]) > 16000:
                raise IngestionError("INTERRUPT_CONTEXT_TOO_LARGE", status=422)
            versions = {source.document_id: source.document_version_id for part in result.chunks for source in part.sources}
            return row, chunk, prepared["text"], versions

    def _documents(self, session):
        documents = []
        for document_id in session.binding.document_ids:
            with self.lessons.documents.document_transaction(document_id) as (_, row):
                if (not row or row["client_id"] != session.binding.client_id or row["deletion_requested_at"]
                        or row["status"] != "DONE" or not row["active_attempt"]
                        or row["metadata"]["course_id"] != session.binding.course_id):
                    raise IngestionError("LESSON_DOCUMENT_UNAVAILABLE", status=409)
                documents.append((document_id, str(row["active_attempt"]), row["metadata"]["language"]))
        return documents

    def _retrieve(self, session, chunk, versions, documents, teacher, intent, message):
        by_id = {value[0]: value for value in documents}
        chunks = []
        for source in chunk.sources:
            if source.document_id in by_id:
                _, token, _ = by_id[source.document_id]
                found = self.vectors.source_chunks(source.document_id, session.binding.course_id,
                                                   token, source.source_chunk_ids)
                if {item["chunk_id"] for item in found} != set(source.source_chunk_ids):
                    raise IngestionError("INTERRUPT_SOURCE_UNAVAILABLE", status=503)
                chunks.extend(found)
        # Canonical current-window evidence is supplied even without semantic search.
        baseline = {(item["document_id"], item["chunk_id"]) for item in chunks}
        if intent.is_course_question or intent.use_course_retrieval:
            groups = defaultdict(list)
            for document_id, token, language in documents:
                groups[language.lower()].append((document_id, token))
            provider = self.embeddings or BedrockTitanEmbeddingProvider(session=aws_session(self.settings),
                model_id=self.settings.embedding_model_id, dimension=self.settings.embedding_dimension,
                client=aws_session(self.settings).client("bedrock-runtime", region_name=self.settings.aws_region,
                    config=Config(connect_timeout=5, read_timeout=self.settings.interrupt_provider_timeout_seconds,
                                  retries={"total_max_attempts": 1, "mode": "standard"})))
            try:
                for language, scope in groups.items():
                    query = teacher.rewrite_query(intent.retrieval_query or message, language)
                    chunks.extend(self.vectors.search_session(provider.embed_query(query), session.binding.course_id,
                                                              scope, limit=6))
            except IngestionError:
                raise
            except Exception:
                raise IngestionError("INTERRUPT_RETRIEVAL_UNAVAILABLE", status=503, retryable=True) from None
        unique, used, seen = [], 0, set()
        for item in chunks:
            key = (item.get("document_id"), item.get("chunk_id"))
            if key in seen:
                continue
            seen.add(key)
            if (key[0] not in by_id or item.get("course_id") != session.binding.course_id
                    or str(item.get("ingestion_token")) != by_id[key[0]][1]
                    or item.get("document_version_id") != versions.get(key[0])):
                raise IngestionError("INTERRUPT_SOURCE_VERSION_MISMATCH", status=409)
            text = item.get("text", "")
            if not text.strip():
                raise IngestionError("INTERRUPT_SOURCE_UNAVAILABLE", status=503)
            if used + len(text) > self.settings.interrupt_source_characters:
                if key not in baseline:
                    continue  # Choose whole evidence chunks; never truncate formulas.
                raise IngestionError("INTERRUPT_SOURCE_CONTEXT_TOO_LARGE", status=422)
            used += len(text)
            unique.append({"chunk_id": key[1], "document_id": key[0], "document_version_id": versions[key[0]],
                           "pages": item["page_numbers"], "text": text,
                           "quality_status": item.get("quality_status", "UNVERIFIED")})
        return unique

    @staticmethod
    def _verified_sources(text, chunks, check):
        lookup = {item["chunk_id"]: item for item in chunks}
        if not check.supported or not check.safe or not check.in_language or not check.evidence:
            return None
        used = {}
        for evidence in check.evidence:
            item = lookup.get(evidence.source_chunk_id)
            if (not item or normalized(evidence.quote) not in normalized(item["text"])
                    or normalized(evidence.claim) not in normalized(text)):
                return None
            used[evidence.source_chunk_id] = item
        cited_pages = set()
        for match in re.finditer(r"\[pp?\.\s*(\d+)(?:\s*[-–]\s*(\d+))?\]", text):
            start, end = int(match[1]), int(match[2] or match[1])
            if end < start or end-start > 100:
                return None
            cited_pages.update(range(start, end+1))
        if not cited_pages or not cited_pages.issubset({page for item in used.values() for page in item["pages"]}):
            return None
        return [LessonSource(document_id=item["document_id"], document_version_id=item["document_version_id"],
                             pages=item["pages"], source_chunk_ids=[item["chunk_id"]],
                             quality_status=item["quality_status"]) for item in used.values()]

    def _run_graph(self, session, data, audio, context, chunk, versions, documents):
        teacher = self.teacher_factory(self.settings, session.binding)
        history = [{"role": item["role"], "text": item["text"][:2000]} for item in session.chat_cache[-12:]]
        messages = MESSAGES.get(session.binding.language.split("-")[0], MESSAGES["en"])

        def transcribe(state):
            if audio is None:
                return {"message": data.text}
            pcm, rate = wav_pcm(audio, self.settings)
            return {"message": self.stt.transcribe(pcm, rate, session.binding.language)}

        def classify(state):
            self.store.ensure_current(session)
            return {"intent": LearnerIntent.model_validate(teacher.classify(state["message"], context, history))}

        def response(state):
            intent = state["intent"]
            value = InterruptResponse(request_id=data.request_id, chunk_id=data.chunk_id,
                playback_action=intent.playback_action,
                answer_id=uuid5(NAMESPACE_URL, f"answer:{session.response.session_id}:{data.request_id}"))
            if intent.operation == "blocked":
                value.text = messages[1]
                value.warnings.append("INPUT_BLOCKED")
            elif intent.operation == "clarify":
                text = teacher.clarify(state["message"], context, history)
                check = teacher.verify(text, [])
                value.text = text if check.safe and check.in_language else messages[2]
            else:
                chunks = self._retrieve(session, chunk, versions, documents, teacher, intent, state["message"])
                if not chunks:
                    sources = None
                    value.text, value.grounded = messages[0], False
                else:
                    text = teacher.respond(state["message"], intent.student_goal, context, history, chunks)
                    if intent.is_course_question:
                        check = teacher.verify(text, chunks)
                        sources = self._verified_sources(text, chunks, check)
                        value.text = text if sources is not None else messages[0]
                        value.grounded = sources is not None
                    else:
                        # v2 teaching support returns the teacher turn directly.
                        # These references describe the supplied course context;
                        # no independent factual verification is claimed.
                        value.text = text
                        sources = [LessonSource(document_id=item["document_id"],
                            document_version_id=item["document_version_id"], pages=item["pages"],
                            source_chunk_ids=[item["chunk_id"]], quality_status=item["quality_status"])
                            for item in chunks]
                value.sources = sources or []
                if sources and any(source.quality_status != "AUTO_CHECKED" for source in sources):
                    value.warnings.append("SOURCE_REQUIRES_REVIEW")
                if sources is None:
                    value.playback_action = None
                    value.warnings.append("ANSWER_NOT_SUPPORTED")
            return {"response": value}

        def player(state):
            return {"response": InterruptResponse(request_id=data.request_id, chunk_id=data.chunk_id,
                                                   playback_action=state["intent"].playback_action)}

        def speak(state):
            value = state["response"]
            self.store.ensure_current(session)
            try:
                speech = self.speech_factory(self.settings, teacher)
                language_code = speech.validate_voice(session.response.voice, session.binding.language)
                ssml, parts, issues = speech.prepare(value.text, session.binding.language)
                audio_bytes, marks, _, _ = speech.synthesize(parts, session.response.voice, language_code)
                if len(audio_bytes) > 4_000_000:
                    raise ValueError("Reply audio limit")
                value.ssml, value.visemes = ssml, marks
                value.audio = InlineAudio(data=base64.b64encode(audio_bytes).decode("ascii"))
                if issues or getattr(speech, "last_timing_issues", []):
                    value.warnings.append("SPEECH_REQUIRES_REVIEW")
            except Exception:
                value.ssml, value.audio, value.visemes = None, None, []
                value.warnings.append("AUDIO_UNAVAILABLE")
            return {"response": value}

        graph = StateGraph(TurnState)
        graph.add_node("transcribe", transcribe)
        graph.add_node("teacher_classify", classify)
        graph.add_node("retrieve_and_respond", response)
        graph.add_node("player_action", player)
        graph.add_node("speech", speak)
        graph.add_edge(START, "transcribe")
        graph.add_edge("transcribe", "teacher_classify")
        graph.add_conditional_edges("teacher_classify", lambda state: "player" if state["intent"].operation == "playback" else "answer",
                                   {"player": "player_action", "answer": "retrieve_and_respond"})
        graph.add_edge("player_action", END)
        graph.add_edge("retrieve_and_respond", "speech")
        graph.add_edge("speech", END)
        # Explicitly disable disk checkpoints and external LangSmith tracing.
        with tracing_context(enabled=False):
            return graph.compile(checkpointer=False).invoke({}, config={"callbacks": [], "recursion_limit": 12})

    def handle(self, session_id, data, principal, token=None, audio=None):
        self.authorized_session(session_id, principal, token)
        if (data.text is None) == (audio is None):
            raise HTTPException(422, "SEND_EXACTLY_ONE_TEXT_OR_AUDIO")
        fingerprint = hashlib.sha256(json.dumps(data.model_dump(mode="json"), sort_keys=True).encode()
                                     + (hashlib.sha256(audio).digest() if audio is not None else b"")).hexdigest()
        request_id = str(data.request_id)
        with self.store.interrupt(session_id, principal.client_id, request_id, fingerprint) as (session, cached):
            row, chunk, context, versions = self._lesson(session, data, principal)
            if cached is not None:
                return cached
            if not self.capacity.acquire(blocking=False):
                raise HTTPException(503, "INTERRUPT_CAPACITY_REACHED", headers={"Retry-After": "2"})
            try:
                documents = self._documents(session)
                state = self._run_graph(session, data, audio, context, chunk, versions, documents)
                value = state["response"]
                # Recheck revocation/expiry after provider calls, immediately before delivery.
                with self.lessons.staging.artifact_lock(session.binding.lesson_job_id, shared=True), ExitStack() as locks:
                    self._lesson(session, data, principal)
                    for document_id, token_value, _ in sorted(documents):
                        _, source = locks.enter_context(self.lessons.documents.document_transaction(document_id))
                        if not source or source["deletion_requested_at"] or str(source["active_attempt"]) != token_value:
                            raise IngestionError("LESSON_DOCUMENT_UNAVAILABLE", status=409)
                    self.store.ensure_current(session)
                    self.authorized_session(session_id, principal, token)
                    self.lessons.audit.learner_interrupt(row, str(session.response.session_id), request_id,
                        {"chunk_id": str(data.chunk_id), "answer_id": str(value.answer_id) if value.answer_id else None,
                         "grounded": value.grounded, "source_count": len(value.sources),
                         "audio": value.audio is not None, "playback_action": value.playback_action.action if value.playback_action else None,
                         "warnings": value.warnings})
                    self.store.finish_interrupt(session, request_id, fingerprint, value, state["message"])
                return value
            except psycopg.Error:
                raise IngestionError("AUDIT_STORAGE_UNAVAILABLE", status=503, retryable=True) from None
            except ValueError:
                raise IngestionError("INTERRUPT_MODEL_INVALID_OUTPUT", status=503, retryable=True) from None
            finally:
                self.capacity.release()
