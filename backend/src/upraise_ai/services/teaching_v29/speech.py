"""Apply the same bounded judge workflow to whole spoken scripts before TTS."""

from upraise_ai.services.teaching_v2.contracts import Narration, structured
from upraise_ai.services.teaching_v2.first_draft import retention_error, storable_text
from upraise_ai.services.teaching_v2.prompts import NARRATION_POLICY
from upraise_ai.services.teaching_v2.review_recovery import gap_text
from upraise_ai.services.teaching_v2.speech import TeachingSpeechV2

from . import workflow


class TeachingSpeechV29(TeachingSpeechV2):
    def narration(self, text, language):
        context = {"kind": "spoken", "written_reference": text, "citation_numbers": [],
                   "scope": "Preserve the whole written lesson in natural speech without adding teaching."}
        def generate():
            return structured(self.models, NARRATION_POLICY, {"written_lesson": text, "language": language},
                              Narration, "teaching_v29_narration_writer")
        try:
            candidate = self.models.first_candidate("narration", [context, language, NARRATION_POLICY], Narration, generate)
        except Exception as error:
            if retention_error(error) is None:
                raise
            candidate = Narration(text=storable_text(text) or gap_text(language))
        final, reports, _ = workflow.adjudicate(self.models, candidate, context, language, stage="narration")
        return final, [issue for report in reports for issue in report.issues]
