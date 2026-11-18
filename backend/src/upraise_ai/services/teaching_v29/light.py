"""V2.9's teaching workflow with Polly Standard audio and speech marks."""

from upraise_ai.core.teaching import speech_settings

from .models import TeachingModelsV29
from .speech import TeachingSpeechV29


class TeachingModelsV29Light(TeachingModelsV29):
    teaching_versions = {**TeachingModelsV29.teaching_versions,
        "teaching_version": "v2.9-light", "speech_policy": "polly-standard-v1"}


class TeachingSpeechV29Light(TeachingSpeechV29):
    def __init__(self, settings, models, *args, **kwargs):
        super().__init__(speech_settings(settings, "v2.9-light"), models, *args, **kwargs)
