"""Opt-in V2.9; existing V2.8 and V3 implementations remain independent."""

from .models import TeachingModelsV29
from .speech import TeachingSpeechV29

__all__ = ["TeachingModelsV29", "TeachingSpeechV29"]
