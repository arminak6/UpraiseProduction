"""Opt-in teaching/narration V2. V1 implementations remain independent."""

from .models import TeachingModelsV2
from .speech import TeachingSpeechV2

__all__ = ["TeachingModelsV2", "TeachingSpeechV2"]
