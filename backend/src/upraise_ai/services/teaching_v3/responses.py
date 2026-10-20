"""Strict complete JSON recovery for V3 calls; unreadable verdicts never approve."""

import json
from pathlib import Path
from typing import Any

from pydantic import RootModel

from upraise_ai.core.errors import IngestionError
from upraise_ai.services.aligned_responses import ResponseFormatError, parse_response
from upraise_ai.services.lesson_pipeline import write_json
from upraise_ai.services.v3.bedrock_teacher import BedrockTeacherV1, TeacherGenerationError


class ObjectResponse(RootModel[dict[str, Any]]):
    pass


class ResponseTeacherV3(BedrockTeacherV1):
    def __init__(self, *, diagnostic_dir=None, **kwargs):
        super().__init__(**kwargs)
        self.response_diagnostics = Path(diagnostic_dir) if diagnostic_dir else None

    def _converse_text(self, **request):
        original = request
        for attempt in range(2):
            if attempt:
                request = {**original, "max_tokens": min(8000, max(4000, original["max_tokens"] * 2)),
                    "user_prompt": original["user_prompt"] +
                        "\nOUTPUT CONTRACT: Re-evaluate the original task and return exactly ONE complete JSON "
                        "object using double-quoted keys and strings. No fences, prose, Python literals, extra "
                        "objects or omitted required fields. Follow the supplied schema. Do not change the task's "
                        "factual criteria to make the response easier to format."}
            problem = None
            try:
                raw = super()._converse_text(**request)
            except TeacherGenerationError as error:
                if "reached maxTokens=" not in str(error):
                    raise
                problem = "TOKEN_LIMIT"
            else:
                try:
                    parsed = parse_response(raw, ObjectResponse)
                    return json.dumps(parsed.root, ensure_ascii=False)
                except ResponseFormatError as error:
                    problem = error.reason
            if self.response_diagnostics:
                schema = (request.get("output_config") or {}).get("textFormat", {}).get("structure", {}).get("jsonSchema", {})
                write_json(self.response_diagnostics / "response-contract.json",
                    {"stage": str(schema.get("name", "json_recovery"))[:100], "problem": problem,
                     "format_attempt": attempt + 1, "max_tokens": request["max_tokens"]})
        raise IngestionError("TEACHING_V3_MODEL_RESPONSE_INVALID", retryable=True)
