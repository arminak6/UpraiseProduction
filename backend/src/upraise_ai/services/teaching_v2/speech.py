"""V2 verifies a separate complete spoken script before any Polly call."""

import hashlib
import json

from upraise_ai.core.errors import IngestionError
from upraise_ai.services.lesson_speech import LessonSpeech

from .contracts import Decision, Narration, structured, validate_spoken, validate_written
from .prompts import NARRATION_POLICY, REVIEW_POLICY, VERSION


class TeachingSpeechV2(LessonSpeech):
    def prepare(self, text, language):
        validate_written(text)
        self.models.transport.language = language
        key = hashlib.sha256(json.dumps([VERSION, NARRATION_POLICY, REVIEW_POLICY, language, text,
            self.settings.lesson_generation_model_id, self.settings.lesson_review_model_id], ensure_ascii=False).encode()).hexdigest()
        folder = self.models.diagnostic_dir.parent / "teaching-v2-narration" if self.models.diagnostic_dir else None
        path = folder / (key + ".json") if folder else None
        cached = json.loads(path.read_text(encoding="utf-8")) if path and path.is_file() else None
        narration = None
        if cached and cached.get("key") == key:
            decision = Decision.model_validate(cached["review"])
            if decision.passed:
                narration = Narration.model_validate(cached["narration"])
                validate_spoken(narration.text)
        if narration is None:
            payload = {"written_lesson": text, "language": language}
            for attempt in range(3):
                narration = structured(self.models, NARRATION_POLICY, payload, Narration,
                                       "teaching_v2_narration")
                try:
                    validate_spoken(narration.text)
                except IngestionError:
                    payload.update(previous_narration=narration.text,
                                   corrections=["Remove mathematical markup, literal escapes and mechanical delimiter reading."])
                    continue
                decision = structured(self.models, REVIEW_POLICY,
                    {"kind": "spoken", "written_lesson": text, "candidate": narration.text,
                     "language": language}, Decision, "teaching_v2_narration_review", reviewer=True)
                if decision.passed:
                    break
                payload.update(previous_narration=narration.text, corrections=decision.issues or
                               ["Restore complete mathematical meaning and natural wording in the requested language."])
            else:
                raise IngestionError("TEACHING_V2_NARRATION_REJECTED", retryable=True)
            if path:
                from upraise_ai.services.lesson_pipeline import write_json
                write_json(path, {"key": key, "narration": narration.model_dump(), "review": decision.model_dump()})
        # Already verbalised mathematics bypasses V1's symbolic converter.
        return super().prepare(narration.text, language)
