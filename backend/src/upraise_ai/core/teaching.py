"""Teaching selections and their course-scoped speech settings."""

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from upraise_ai.core.config import Settings

TeachingVersion = Literal["v1", "v2", "v2.9", "v2.9-light", "v3", "v3.1"]


def speech_settings(settings: "Settings", teaching_version: str | None) -> "Settings":
    if teaching_version == "v2.9-light":
        return settings.model_copy(update={"polly_engine": "standard"})
    return settings
