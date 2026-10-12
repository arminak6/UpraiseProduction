"""API wrapper for the original Uprise/v2 lecture teacher, without extra judges."""

import json
import logging
from pathlib import Path
from uuid import uuid4

from botocore.config import Config
from pydantic import ValidationError

from upraise_ai.core.errors import IngestionError
from upraise_ai.schemas.lessons import ReviewReport
from upraise_ai.services.aws import aws_session
from upraise_ai.services.lesson_compat import legacy_review_report, turn_to_draft
from upraise_ai.services.lesson_prompts import LEARNER_LEVEL_PROMPT_VERSION, learner_level_system_prompt
from upraise_ai.services.lesson_translation import LessonTranslator, language_name
from upraise_ai.services.v3.bedrock_teacher import BedrockTeacherV1, TeacherGenerationError
from upraise_ai.services.v3.math_speech import MathNarrationRequest
from upraise_ai.services.v3.models import EASY_LECTURE_PROMPT_VERSION, LearningChunk, LearningDocument

DRAFT_PROMPT = f"{EASY_LECTURE_PROMPT_VERSION}-{LEARNER_LEVEL_PROMPT_VERSION}"
REVIEW_PROMPT = f"learning-v1-source-review-original-{LEARNER_LEVEL_PROMPT_VERSION}"
FINALIZER_PROMPT = f"learning-v1-final-editor-original-{LEARNER_LEVEL_PROMPT_VERSION}"
VISUAL_PROMPT = "not-used-v3"
MATH_PROMPT = "math-narration-v3"
log = logging.getLogger("upraise.lesson_models")


class _ContentFreeLogs(logging.Filter):
    def filter(self, record):
        # Legacy recovery logs interpolate exception strings containing generated
        # content. Keep vendor functions intact and remove that data at the sink.
        record.msg, record.args = "V3 teacher recovery or validation event", ()
        record.exc_info = None
        return True


logging.getLogger("upraise_ai.services.v3.bedrock_teacher").addFilter(_ContentFreeLogs())


def merge_reviews(reviews, summary="V3 source review and speech checks; human approval is required."):
    issues = [issue for review in reviews for issue in review.issues]
    instructions = list(dict.fromkeys(i for review in reviews for i in review.revision_instructions))
    return ReviewReport(approved=not issues, summary=summary, issues=issues,
                        revision_instructions=instructions)


class _LanguageClient:
    def __init__(self, client):
        self.client = client
        self.language = "en"
        self.teaching_prompt = None
        self.raw = ""
        self.stop_reason = None

    def converse(self, **request):
        # Keep V3 schemas/recovery intact; add trusted API teaching policy only
        # during generation, review and editing, not mathematical narration.
        if self.teaching_prompt is not None:
            request["system"] = [*request["system"], {"text": self.teaching_prompt}]
        if self.language.lower() not in {"en", "en-us", "en-gb"}:
            request["system"] = [*request["system"], {"text":
                f"Write all teaching text and mathematical narration in {language_name(self.language)}. "
                "Retain the exact source notation and JSON contract field names."}]
        self.raw = ""
        self.stop_reason = None
        response = self.client.converse(**request)
        self.stop_reason = response.get("stopReason")
        blocks = response.get("output", {}).get("message", {}).get("content", [])
        self.raw = "\n".join(block["text"] for block in blocks
                             if isinstance(block, dict) and isinstance(block.get("text"), str))
        return response


class LessonModels:
    def __init__(self, settings, client=None, *, diagnostic_dir=None):
        self.settings = settings
        self.diagnostic_dir = Path(diagnostic_dir) if diagnostic_dir is not None else None
        client = client or aws_session(settings).client("bedrock-runtime", region_name=settings.aws_region,
            config=Config(retries={"max_attempts": 6, "mode": "adaptive"}, connect_timeout=10, read_timeout=180))
        self.client = client
        self.translator = LessonTranslator(settings, client)
        self.transport = _LanguageClient(client)
        self.teacher = BedrockTeacherV1(session=None, client=self.transport,
            model_id=settings.lesson_generation_model_id, reviewer_model_id=settings.lesson_review_model_id)

    @staticmethod
    def source(section):
        version = section.get("document_version_id", "selected-version")
        document_id = section.get("document_id", "selected-document")
        title = next((chunk.get("document_title") for chunk in section["chunks"]
                      if chunk.get("document_title")), "selected document")
        document = LearningDocument(document_id=document_id, document_version_id=version,
            document_title=title, total_pages=section.get("page_count", max(section["pages"])),
            course_id=section.get("course_id", ""))
        chunks = [LearningChunk(chunk_id=chunk["chunk_id"], chunk_index=chunk.get("chunk_index", index),
            document_id=document_id, document_version_id=version, text=chunk["text"],
            page_numbers=chunk["page_numbers"], heading_path=chunk.get("heading_path", []))
            for index, chunk in enumerate(section["chunks"])]
        start, end = section.get("window", [min(section["pages"]), max(section["pages"])])
        return document, {"page_start": start, "page_end": end, "chunks": chunks}

    def diagnose(self, phase, error):
        cause = error
        while cause.__cause__ is not None:
            cause = cause.__cause__
        errors = (cause.errors(include_input=False, include_url=False, include_context=False)
                  if isinstance(cause, ValidationError) else [{"type": type(cause).__name__}])
        log.warning("V3 lesson failed phase=%s type=%s", phase, type(cause).__name__)
        if (self.settings.lesson_capture_model_outputs and self.diagnostic_dir is not None
                and self.settings.environment == "development" and self.settings.local_test_mode):
            try:
                self.diagnostic_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
                path = self.diagnostic_dir / f"{uuid4()}.json"
                temporary = path.with_suffix(".part")
                temporary.write_text(json.dumps({"phase": phase, "errors": errors,
                    "raw_output": self.transport.raw[:200_000]}, ensure_ascii=False), encoding="utf-8")
                temporary.replace(path)
            except OSError:
                log.warning("V3 lesson diagnostic storage unavailable")

    def generate_section(self, section, language, source_path=None, *, learner_level="intermediate", notify=None, level_profiles=None):
        self.transport.language = language
        teaching_prompt = learner_level_system_prompt(learner_level, "generator", profiles=level_profiles)
        self.transport.raw = ""
        document, arguments = self.source(section)
        phase = "draft"
        try:
            self.transport.teaching_prompt = teaching_prompt
            draft = self.teacher.explain(document, **arguments)
            phase = "review"
            if notify:
                notify("REVIEW")
            self.transport.teaching_prompt = learner_level_system_prompt(learner_level, "reviewer", profiles=level_profiles)
            review = self.teacher.review_explanation(document, draft=draft, **arguments)
            phase = "finalize"
            if notify:
                notify("FINALIZE")
            self.transport.teaching_prompt = learner_level_system_prompt(learner_level, "editor", profiles=level_profiles)
            # V3 always finalizes, including an approved draft: this is where its
            # citation/markup checks belong. There is no additional post-edit judge.
            final = self.teacher.finalize_explanation(document, draft=draft, review=review, **arguments)
            return turn_to_draft(final), legacy_review_report(review, final.source_pages), int(not review.approved)
        except Exception as error:
            self.diagnose(phase, error)
            code = "LESSON_MODEL_INVALID_OUTPUT"
            if isinstance(error, TeacherGenerationError) and "reached maxTokens" in str(error):
                code = "LESSON_MODEL_INCOMPLETE"
            raise IngestionError(code, retryable=True) from None
        finally:
            self.transport.teaching_prompt = None

    def narrate_math_fallback(self, requests, language):
        self.transport.language = language
        return self.teacher.narrate_math_fallback(requests)

    def translate_section(self, draft, source_language, target_language, *, learner_level):
        try:
            return self.translator.translate(draft, source_language, target_language, learner_level)
        except IngestionError as error:
            # Keep candidate and verifier replies together in the existing
            # opt-in diagnostic capture; the last verifier alone hides defects.
            self.transport.raw = json.dumps(self.translator.outputs, ensure_ascii=False)
            self.diagnose("translation", error)
            raise

    def math_narration(self, latex, context, language):
        return self.narrate_math_fallback([MathNarrationRequest(latex, context)], language)[0]

    def revise_chunk(self, section, original, feedback, neighbours, *, learner_level, notify):
        from upraise_ai.services.revision_models import revise_chunk

        return revise_chunk(self, section, original, feedback, neighbours, learner_level=learner_level, notify=notify)

    def adapt_units(self, section, units, language, *, learner_level, profiles, target_words, max_words, notify=None, current=None):
        from upraise_ai.services.aligned_models import adapt_units
        try:
            return adapt_units(self, section, units, language, learner_level=learner_level, profiles=profiles,
                               target_words=target_words, max_words=max_words, notify=notify, current=current)
        except IngestionError as error:
            self.diagnose("alignment:" + error.stage if hasattr(error, "stage") else "alignment", error)
            raise
