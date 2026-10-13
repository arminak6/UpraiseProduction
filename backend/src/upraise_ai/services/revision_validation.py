"""Retained A7 alignment findings and bounded feedback-driven correction."""

import hashlib

from upraise_ai.core.errors import IngestionError
from upraise_ai.schemas.aligned_lessons import AlignedReview, AlignedText, AlignedTexts, ContentUnit
from upraise_ai.services.aligned_models import accepted, validate_material_format
from upraise_ai.services.lesson_pipeline import write_json
from upraise_ai.services.revision_models import correct_revision, validate_revision_text

MAX_ALIGNMENT_CORRECTIONS = 2


def bounded_text(value, limit=600):
    return " ".join(str(value).split())[:limit]


class RevisionAlignmentRejected(IngestionError):
    def __init__(self, review, *, learner_level, chunk_id, chunk_index, correction_rounds):
        super().__init__("REVISION_ALIGNMENT_REJECTED")
        finding = review.units[0]
        report = {key: getattr(finding, key) for key in ("material_preserved", "level_suitable", "in_language")}
        report.update(issues=[bounded_text(value) for value in finding.issues[:5]],
                      revision_instructions=[bounded_text(value) for value in finding.revision_instructions[:5]])
        fallback = [message for key, message in (
            ("material_preserved", "Required teaching content was changed or omitted."),
            ("level_suitable", "The explanation does not match the requested difficulty."),
            ("in_language", "The text does not match the requested language.")) if not report[key]]
        reasons = report["issues"] or report["revision_instructions"] or fallback or ["The reviewer requested further changes."]
        self.details = {"stage": "alignment_review", "learner_level": learner_level,
                        "chunk_id": chunk_id, "chunk_index": chunk_index, "correction_rounds": correction_rounds,
                        "max_correction_rounds": MAX_ALIGNMENT_CORRECTIONS, "reason": bounded_text("; ".join(reasons), 1200),
                        "review": report}


def validate_aligned_revision(models, section, data, selected, neighbours, saved, path, progress, reviewer):
    unit = ContentUnit.model_validate(data.revision_request.aligned_unit)
    rounds = saved.get("correction_rounds", 0)
    if not isinstance(rounds, int) or isinstance(rounds, bool) or not 0 <= rounds <= MAX_ALIGNMENT_CORRECTIONS:
        raise IngestionError("REVISION_CHECKPOINT_CONFLICT")
    section["revision_context"] = {"teacher_feedback": data.revision_request.teacher_review,
                                   "original_chunk": selected.text}
    while True:
        text = validate_material_format(unit, validate_revision_text(saved["text"]))
        if len(text.split()) > data.options.max_chunk_words:
            raise IngestionError("REVISION_UNIT_TOO_LARGE")
        saved["text"] = text
        digest = hashlib.sha256(text.encode()).hexdigest()
        cached = saved.get("alignment_review")
        if cached and saved.get("alignment_text_sha256") == digest:
            review = AlignedReview.model_validate(cached)
        else:
            progress("REVIEW", 0.50 + 0.04 * rounds)
            models.transport.language = data.language
            models.transport.teaching_prompt = None
            review = reviewer(models, section, [unit],
                AlignedTexts(units=[AlignedText(content_unit_id=unit.content_unit_id, text=text)]),
                data.language, data.options.learner_level, data.level_profiles)
            saved.update(alignment_review=review.model_dump(mode="json"), alignment_text_sha256=digest)
        if len(review.units) != 1 or review.units[0].content_unit_id != unit.content_unit_id:
            raise IngestionError("REVISION_REVIEW_INTEGRITY_FAILURE")
        saved["correction_rounds"] = rounds
        write_json(path, saved)
        if accepted(review):
            return text, rounds
        if rounds >= MAX_ALIGNMENT_CORRECTIONS:
            raise RevisionAlignmentRejected(review, learner_level=data.options.learner_level,
                chunk_id=selected.chunk_id, chunk_index=selected.index, correction_rounds=rounds)
        progress("FINALIZE", 0.52 + 0.04 * rounds)
        rounds += 1
        # Reserve the correction before calling the provider, bounding attempts
        # across retries or child termination. A saved accepted candidate is reused.
        saved["correction_rounds"] = rounds
        write_json(path, saved)
        saved["text"] = correct_revision(models, section, text, data.revision_request.teacher_review,
                                          neighbours, review, data.language, data.options.learner_level)
        saved["edits"] = 1
        write_json(path, saved)
