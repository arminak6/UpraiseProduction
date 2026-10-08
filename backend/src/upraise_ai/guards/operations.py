"""Operator controls apply to new AI work, preserving playback and session cleanup."""

from upraise_ai.core.errors import IngestionError


def kill_switch_active(settings, course_id=None):
    return settings.operations_kill_switch or course_id in settings.operations_kill_switch_courses


def feature_enabled(settings, feature, course_id=None):
    return not (kill_switch_active(settings, course_id)
                or feature in settings.operations_disabled_features
                or feature in settings.operations_course_disabled_features.get(course_id, set()))


def require_feature(settings, feature, course_id=None):
    if kill_switch_active(settings, course_id):
        raise IngestionError("AI_DISABLED", status=503)
    if not feature_enabled(settings, feature, course_id):
        raise IngestionError(f"{feature.upper()}_DISABLED", status=503)


def blocked_generation_courses(settings):
    return sorted(settings.operations_kill_switch_courses | {
        course for course, features in settings.operations_course_disabled_features.items()
        if features & {"generation", "tts"}
    })


def require_ai(settings, course_id=None):
    if kill_switch_active(settings, course_id):
        raise IngestionError("AI_DISABLED", status=503)
