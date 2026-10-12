"""C5 describes deployed configuration, independently of provider readiness."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class VersionItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str | None
    sha256: str | None
    hash_kind: Literal["source_bundle_sha256"] = "source_bundle_sha256"
    status: Literal["AVAILABLE", "UNAVAILABLE"]


class ModelVersion(BaseModel):
    provider: str
    model_id: str | None = None
    engine: str | None = None
    version_status: Literal["CONFIGURED", "PROVIDER_MANAGED"] = "CONFIGURED"


class ApplicationVersion(BaseModel):
    version: str
    build_id: str | None
    build_id_kind: Literal["source_and_locks_sha256", "source_sha256", "UNAVAILABLE"]
    dependencies: dict[str, str | None]


class PipelineVersion(BaseModel):
    version: str
    parameters: dict[str, str | int | bool]


class FusionRule(BaseModel):
    status: Literal["NOT_IMPLEMENTED"] = "NOT_IMPLEMENTED"
    version: None = None


class ThresholdMethod(BaseModel):
    name: Literal["fixed_threshold"] = "fixed_threshold"
    version: str
    value: float
    comparison: Literal[">"] = ">"
    telemetry_consent_required: bool = True


class CognitiveLoadVersion(BaseModel):
    fusion_rule: FusionRule
    threshold_method: ThresholdMethod


class VersionStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["c5-v1"] = "c5-v1"
    application: ApplicationVersion
    models: dict[str, ModelVersion]
    prompts: dict[str, VersionItem]
    pipelines: dict[str, PipelineVersion]
    cognitive_load: CognitiveLoadVersion
    configuration_id: str
