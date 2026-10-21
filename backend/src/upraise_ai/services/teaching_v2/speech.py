"""Prepare narration before synthesis and retain unresolved DRAFT review flags."""

import hashlib
import json

from upraise_ai.core.errors import IngestionError
from upraise_ai.schemas.lessons import ReviewIssue
from upraise_ai.services.lesson_pipeline import write_json
from upraise_ai.services.lesson_speech import LessonSpeech

from .contracts import Decision, Narration, structured, validate_spoken, validate_written
from .first_draft import retention_error, storable_text
from .prompts import NARRATION_POLICY, REVIEW_POLICY, VERSION
from .review_recovery import POLICY, gap_text, review_failure, review_flag


class TeachingSpeechV2(LessonSpeech):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.narrations, self.review_warnings = {}, {}

    def prepare_course(self, by_level, language, signature, progress):
        for values in by_level.values():
            for value in values:
                progress("REVIEW", .59)
                _, _, issues = self.prepare(value[1], language)
                self.review_warnings[str(value[0])] = issues

    def review_issues(self, chunk_id):
        return self.review_warnings.get(str(chunk_id), [])

    def narration(self, text, language):
        first = self.models.preserve_first_draft
        if not first:
            validate_written(text)
        self.models.transport.language = language
        recovery = self.models.draft_review_recovery
        key = hashlib.sha256(json.dumps([VERSION, self.models.teaching_versions.get("draft_review_recovery", POLICY),
            recovery, NARRATION_POLICY, REVIEW_POLICY, language, text,
            self.settings.lesson_generation_model_id, self.settings.lesson_review_model_id], ensure_ascii=False).encode()).hexdigest()
        if key in self.narrations:
            return self.narrations[key]
        folder = self.models.diagnostic_dir.parent / "teaching-v2-narration" if self.models.diagnostic_dir else None
        path = folder / (key + ".json") if folder else None
        if path and path.is_file():
            try:
                cached = json.loads(path.read_text(encoding="utf-8"))
                candidate = Narration.model_validate(cached["narration"])
                issues = [ReviewIssue.model_validate(w) for w in cached.get("warnings", [])]
                accepted = cached.get("review_status") == "passed" or (
                    recovery and cached.get("review_status") == "needs_human_review" and issues)
                if not (first and cached.get("review_status") == "needs_human_review" and issues):
                    validate_spoken(candidate.text)
                elif storable_text(candidate.text) != candidate.text:
                    accepted = False
                if cached.get("key") == key and accepted:
                    self.narrations[key] = candidate, issues
                    return candidate, issues
            except (ValueError, KeyError, TypeError, IngestionError):
                pass
        payload = {"written_lesson": text, "language": language}
        retained, issues, problems = None, [], []
        try:
            for attempt in range(3):
                def generate():
                    return structured(self.models, NARRATION_POLICY, payload, Narration, "teaching_v2_narration")
                candidate = (self.models.first_candidate("narration", [NARRATION_POLICY, payload], Narration, generate)
                             if first else generate())
                if first:
                    safe = storable_text(candidate.text)
                    retained = Narration(text=safe if safe.strip() else storable_text(text) or gap_text(language))
                try:
                    validate_spoken(candidate.text)
                except IngestionError:
                    problems = ["Remove mathematical markup, literal escapes and mechanical delimiter reading."]
                    if first:
                        raise IngestionError("TEACHING_V2_NARRATION_REJECTED") from None
                    payload.update(previous_narration=candidate.text, corrections=problems)
                    continue
                retained = candidate
                decision = structured(self.models, REVIEW_POLICY,
                    {"kind": "spoken", "written_lesson": text, "candidate": candidate.text,
                     "language": language}, Decision, "teaching_v2_narration_review", reviewer=True)
                if decision.passed:
                    break
                problems = decision.issues or ["Restore complete mathematical meaning and natural wording in the requested language."]
                if first:
                    raise IngestionError("TEACHING_V2_NARRATION_REJECTED")
                payload.update(previous_narration=candidate.text, corrections=problems)
            else:
                raise IngestionError("TEACHING_V2_NARRATION_REJECTED", retryable=True)
        except Exception as error:
            if first:
                failure = retention_error(error)
                if failure is None:
                    raise
            elif not recovery or not isinstance(error, IngestionError) or not review_failure(error):
                raise
            else:
                failure = error
            retained = retained or Narration(text=storable_text(text) if first and storable_text(text).strip() else gap_text(language))
            issues = [review_flag("narration review", failure, category="speech", issues=problems)]
        if path:
            write_json(path, {"key": key, "narration": retained.model_dump(),
                              "review_status": "needs_human_review" if issues else "passed",
                              "warnings": [w.model_dump() for w in issues]})
        self.narrations[key] = retained, issues
        return retained, issues

    def prepare(self, text, language):
        narration, warnings = self.narration(text, language)
        # Already verbalised mathematics bypasses V1's symbolic converter.
        candidates = [narration.text]
        if self.models.preserve_first_draft:
            candidates.extend([storable_text(text), gap_text(language)])
        for candidate in candidates:
            try:
                full, parts, issues = super().prepare(candidate, language)
                break
            except Exception as error:
                failure = (IngestionError("TEACHING_V2_NARRATION_INVALID") if isinstance(error, IngestionError)
                           and error.code == "LESSON_SPEECH_EMPTY" else retention_error(error))
                if not self.models.preserve_first_draft or failure is None or candidate == candidates[-1]:
                    raise
                warnings = [*warnings, review_flag("narration rendering; existing writing retained", failure, category="speech")]
        return full, parts, [*issues, *warnings]
