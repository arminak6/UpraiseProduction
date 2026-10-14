"""Admin authorization, durable C3 updates and effective global/course state."""

import psycopg
from fastapi import HTTPException

from upraise_ai.core.errors import IngestionError
from upraise_ai.guards.operations import control_state
from upraise_ai.schemas.operations import CONTROL_FEATURES, KillSwitchState
from upraise_ai.services.versions import VersionCatalog
from upraise_ai.storage.controls import ControlsRepository


class ControlService:
    def __init__(self, settings, *, repository=None, catalog=None):
        self.settings = settings
        self.repository = repository or ControlsRepository(settings)
        self.catalog = catalog

    def update(self, data, principal):
        principal.require_scope("admin:operations")
        if principal.local_test and not (self.settings.environment == "development"
                                        and self.settings.local_test_mode and self.settings.operations_local_admin):
            raise HTTPException(403, "LOCAL_ADMIN_NOT_ENABLED")
        if data.course_id is not None:
            principal.require_course(data.course_id)
        if not self.settings.jobs_dsn.get_secret_value():
            raise IngestionError("OPERATIONS_STORAGE_NOT_CONFIGURED", status=503)
        catalog = self.catalog or VersionCatalog(self.settings)
        try:
            event, rows = self.repository.update(data, principal.client_id, catalog.snapshot().model_dump(mode="json"))
        except psycopg.Error:
            raise IngestionError("OPERATIONS_STORAGE_UNAVAILABLE", status=503, retryable=True) from None
        state = control_state(self.settings, rows=rows)
        projected = self.repository.project(event)
        return KillSwitchState(**data.model_dump(), kill_switch_active=state.killed(data.course_id),
            features_enabled={feature: state.enabled(feature, data.course_id) for feature in CONTROL_FEATURES},
            updated_at=event["created_at"], audit_event_id=event["event_id"], audit_pending=not projected)
