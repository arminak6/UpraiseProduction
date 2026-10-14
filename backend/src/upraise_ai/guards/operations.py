"""Shared runtime disables combine with deployment controls; storage failures close AI admission."""

from dataclasses import dataclass

import psycopg

from upraise_ai.core.errors import IngestionError
from upraise_ai.storage.controls import ControlsRepository


@dataclass(frozen=True)
class ControlState:
    global_disabled: frozenset[str]
    courses: dict[str, frozenset[str]]

    def killed(self, course_id=None):
        return "all" in self.global_disabled or "all" in self.courses.get(course_id, ())

    def enabled(self, feature, course_id=None):
        return not (self.killed(course_id) or feature in self.global_disabled
                    or feature in self.courses.get(course_id, ()))

    def blocked_courses(self, features):
        return sorted(course for course, disabled in self.courses.items()
                      if "all" in disabled or disabled.intersection(features))


def control_state(settings, *, rows=None):
    global_disabled = set(settings.operations_disabled_features)
    if settings.operations_kill_switch:
        global_disabled.add("all")
    courses = {course: set(features) for course, features in settings.operations_course_disabled_features.items()}
    for course in settings.operations_kill_switch_courses:
        courses.setdefault(course, set()).add("all")
    if rows is None:
        try:
            rows = ControlsRepository(settings).read() if settings.jobs_dsn.get_secret_value() else []
        except psycopg.Error:
            raise IngestionError("OPERATIONS_STORAGE_UNAVAILABLE", status=503, retryable=True) from None
    for row in rows:
        target = global_disabled if row["scope_key"] == "" else courses.setdefault(row["scope_key"], set())
        target.add(row["feature"])
    return ControlState(frozenset(global_disabled), {course: frozenset(features) for course, features in courses.items()})


def kill_switch_active(settings, course_id=None, *, state=None):
    return (state or control_state(settings)).killed(course_id)


def feature_enabled(settings, feature, course_id=None, *, state=None):
    return (state or control_state(settings)).enabled(feature, course_id)


def require_feature(settings, feature, course_id=None):
    state = control_state(settings)
    if state.killed(course_id):
        raise IngestionError("AI_DISABLED", status=503)
    if not state.enabled(feature, course_id):
        raise IngestionError(f"{feature.upper()}_DISABLED", status=503)


def blocked_generation_courses(settings, *, state=None):
    return (state or control_state(settings)).blocked_courses({"generation", "tts"})


def require_ai(settings, course_id=None):
    if control_state(settings).killed(course_id):
        raise IngestionError("AI_DISABLED", status=503)
