"""The Light selection uses Standard without changing other courses' engines."""

import io
import wave

import pytest
from test_a3 import Polly, request_data

from upraise_ai.core.config import Settings
from upraise_ai.core.errors import IngestionError
from upraise_ai.schemas.aligned_lessons import AlignedLessonInput
from upraise_ai.schemas.lessons import LessonInput
from upraise_ai.services.teaching_v2.contracts import Narration
from upraise_ai.services.teaching_v29 import (
    TeachingModelsV29,
    TeachingModelsV29Light,
    TeachingSpeechV29,
    TeachingSpeechV29Light,
)


@pytest.mark.parametrize("schema", [LessonInput, AlignedLessonInput])
def test_light_is_an_explicit_generation_choice_with_unchanged_default(schema):
    assert schema.model_validate(request_data(teaching_version="v2.9-light")).teaching_version == "v2.9-light"
    assert schema.model_validate(request_data()).teaching_version == "v1"


def test_light_audio_and_marks_use_standard_and_regular_v29_stays_neural(monkeypatch):
    class Client(Polly):
        def describe_voices(self, **kwargs):
            assert kwargs["Engine"] == "standard"
            return {"Voices": [{"Id": "Joanna", "LanguageCode": "en-US", "SupportedEngines": ["standard"]}]}

    settings, client = Settings(), Client()
    models = TeachingModelsV29Light(settings, client=object())
    speech = TeachingSpeechV29Light(settings, models, client=client)
    monkeypatch.setattr(speech, "narration", lambda *args: (Narration(text="A supported spoken explanation."), []))
    code = speech.validate_voice("Joanna", "en")
    _, parts, _ = speech.prepare("A supported written explanation.", "en")
    audio, marks, duration, _ = speech.synthesize(parts, "Joanna", code)
    with wave.open(io.BytesIO(audio)) as wav:
        assert wav.getframerate() == 16000 and wav.getnframes() / 16000 == duration
    assert marks and speech.last_engine == "standard"
    assert {call["Engine"] for call in client.calls} == {"standard"}
    assert {call["OutputFormat"] for call in client.calls} == {"pcm", "json"}
    assert models.teaching_versions["teaching_version"] == "v2.9-light"
    assert models.teaching_versions["teaching_policy"] == "teaching-narration-v2.9"
    regular = TeachingModelsV29(settings, client=object())
    regular_speech = TeachingSpeechV29(settings, regular, client=Polly())
    assert settings.polly_engine == regular_speech.settings.polly_engine == "neural"
    assert regular.teaching_versions["teaching_version"] == "v2.9"
    assert "speech_policy" not in regular.teaching_versions


def test_light_rejects_neural_only_voice_instead_of_using_more_expensive_engine():
    speech = TeachingSpeechV29Light(Settings(), object(), client=Polly())
    with pytest.raises(IngestionError, match="LESSON_VOICE_LANGUAGE_UNSUPPORTED"):
        speech.validate_voice("Joanna", "en")
