from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="UPRAISE_", extra="ignore", hide_input_in_errors=True)

    environment: Literal["production", "development"] = "production"
    local_test_mode: bool = False

    @model_validator(mode="after")
    def local_mode_requires_development(self):
        if self.local_test_mode and self.environment != "development":
            raise ValueError("local_test_mode requires environment=development")
        return self

    jobs_dsn: SecretStr = SecretStr("")
    audit_dsn: SecretStr = SecretStr("")
    data_dir: Path = Path("/data/ingestion")
    qdrant_url: str = "http://qdrant:6333"
    qdrant_collection: str = "upraise_course_chunks_titan_v2_1024"
    aws_region: str = "eu-central-1"
    aws_credentials_file: Path = Path("/run/secrets/aws_credentials.json")
    embedding_model_id: str = "amazon.titan-embed-text-v2:0"
    embedding_dimension: int = 1024
    vision_model_id: str = "eu.amazon.nova-lite-v1:0"
    math_vision_model_id: str = "eu.amazon.nova-pro-v1:0"
    fidelity_vision_model_id: str = "eu.amazon.nova-pro-v1:0"
    enable_ocr: bool = True
    enable_formula_enrichment: bool = True
    describe_images: bool = True
    max_pdf_size_mb: int = Field(100, ge=1, le=500)
    max_pdf_pages: int = Field(500, ge=1, le=5000)
    chunk_max_tokens: int = Field(600, ge=100, le=2000)
    chunk_overlap_tokens: int = Field(60, ge=0)
    pipeline_version: str = "pdf-ingestion-v1.4"
    max_attempts: int = Field(3, ge=1, le=10)
    lease_seconds: int = Field(120, ge=30)
    worker_poll_seconds: float = Field(2, ge=0.1)
    # Runtime budgets limit a complete attempt; they never skip source regions.
    job_timeout_seconds: float = Field(43_200, ge=1, allow_inf_nan=False)
    parse_timeout_base_seconds: float = Field(180, ge=0, allow_inf_nan=False)
    parse_timeout_per_page_seconds: float = Field(10, gt=0, allow_inf_nan=False)
    parse_timeout_per_ocr_page_seconds: float = Field(30, gt=0, allow_inf_nan=False)
    enrich_timeout_base_seconds: float = Field(180, ge=0, allow_inf_nan=False)
    enrich_timeout_per_region_seconds: float = Field(180, gt=0, allow_inf_nan=False)
    embed_timeout_base_seconds: float = Field(120, ge=0, allow_inf_nan=False)
    embed_timeout_per_chunk_seconds: float = Field(90, gt=0, allow_inf_nan=False)
    worker_startup_timeout_seconds: float = Field(300, gt=0, allow_inf_nan=False)
    worker_phase_timeout_seconds: float = Field(300, gt=0, allow_inf_nan=False)
    worker_supervisor_poll_seconds: float = Field(1, ge=0.01, allow_inf_nan=False)
    worker_status_timeout_seconds: float = Field(2, gt=0, allow_inf_nan=False)
    worker_terminate_grace_seconds: float = Field(5, gt=0, allow_inf_nan=False)
    # Zero retains evidence until an operator sets an agreed retention period.
    artifact_retention_days: int = Field(0, ge=0)

    lesson_generation_model_id: str = "openai.gpt-oss-120b-1:0"
    lesson_review_model_id: str = "qwen.qwen3-235b-a22b-2507-v1:0"
    lesson_visual_model_id: str = "eu.amazon.nova-pro-v1:0"  # Retained legacy key; unused by native V3 A3.
    lesson_pipeline_version: str = "lesson-v3-a3-12"
    lesson_v2_pipeline_version: str = "aligned-lessons-v2.5"
    lesson_level_profiles: dict[str, str] = Field(default_factory=lambda: {
        "beginner": "Use plain language, define unfamiliar terms, and explain prerequisites and reasoning step by step. Retain substantive formulas and facts.",
        "intermediate": "Assume basic familiarity; balance intuition with technical detail and explain key reasoning steps and new notation.",
        "advanced": "Assume a strong foundation; use precise terminology and formal notation, explaining source-supported derivations, assumptions and limitations.",
    })
    lesson_plan_level: str = "intermediate"
    lesson_alignment_batch_units: int = Field(4, ge=1, le=8)
    lesson_capture_model_outputs: bool = False
    lesson_ttl_seconds: int = Field(86400, ge=60)
    lesson_source_wait_seconds: int = Field(86400, ge=60)
    lesson_timeout_seconds: float = Field(43200, ge=1, allow_inf_nan=False)
    lesson_seconds_per_section: float = Field(1200, gt=0, allow_inf_nan=False)
    lesson_seconds_per_chunk: float = Field(180, gt=0, allow_inf_nan=False)
    lesson_max_source_characters: int = Field(120000, ge=1000)

    @model_validator(mode="after")
    def valid_level_profiles(self):
        import re
        profiles = self.lesson_level_profiles
        if (not 1 <= len(profiles) <= 8 or self.lesson_plan_level not in profiles
                or any(not re.fullmatch(r"[a-z][a-z0-9_]{0,39}", name) or not text.strip()
                       or len(text) > 4000 for name, text in profiles.items())):
            raise ValueError("lesson_level_profiles requires 1-8 named teaching profiles and a matching lesson_plan_level")
        return self
    polly_engine: Literal["neural", "standard"] = "neural"
    lesson_public_base_url: str = ""
    callback_allowed_hosts: list[str] = []
    callback_allowed_cidrs: list[str] = []
    callback_token_url: str = ""
    callback_client_id: str = ""
    callback_client_secret: SecretStr = SecretStr("")
    callback_oauth_auth_method: Literal["client_secret_basic", "client_secret_post", "tls_client_auth"] = "client_secret_basic"
    callback_scope: str = "lessons:deliver"
    callback_certificate: Path | None = None
    callback_private_key: Path | None = None
    callback_ca_file: Path | None = None
    callback_max_attempts: int = Field(8, ge=1, le=20)
    callback_max_token_lifetime_seconds: int = Field(900, ge=60, le=3600)

    @model_validator(mode="after")
    def lesson_capture_requires_local_testing(self):
        if self.lesson_capture_model_outputs and not (self.environment == "development" and self.local_test_mode):
            raise ValueError("lesson_capture_model_outputs requires development local_test_mode")
        return self

    @field_validator("callback_certificate", "callback_private_key", "callback_ca_file", mode="before")
    @classmethod
    def empty_callback_path(cls, value):
        return None if value == "" else value

    @field_validator("lesson_generation_model_id", "lesson_review_model_id", "lesson_visual_model_id")
    @classmethod
    def lesson_model_must_stay_in_eu(cls, value):
        if value.startswith(("global.", "us.", "apac.")):
            raise ValueError("lesson models require EU or in-region inference")
        if value.startswith("arn:"):
            region = value.split(":")[3]
            if region not in {"eu-central-1", "eu-central-2", "eu-west-1", "eu-west-3",
                              "eu-north-1", "eu-south-1", "eu-south-2"}:
                raise ValueError("lesson model ARN must use an EU region")
        return value

    oauth_issuer: str = ""
    oauth_audience: str = ""
    oauth_jwks_url: str = ""
    oauth_clients: list[str] = []
    trusted_proxy_cidrs: list[str] = []
    # The TLS gateway must overwrite these headers, not forward client values.
    mtls_verify_header: str = "x-ssl-client-verify"
    mtls_fingerprint_header: str = "x-ssl-client-fingerprint"
    mtls_allowed_fingerprints: list[str] = []
    moodle_allowed_cidrs: list[str] = []

    # B1 learner JWTs are separate from the Middleware service access token.
    session_jwt_issuer: str = ""
    session_jwt_audience: str = "upraise-learner-session"
    session_jwt_jwks_url: str = ""
    session_ttl_seconds: int = Field(1800, ge=60, le=86400)
    session_max_count: int = Field(10000, ge=1, le=100000)
    session_sweep_seconds: float = Field(30, ge=1, le=60, allow_inf_nan=False)
    interrupt_max_concurrent: int = Field(4, ge=1, le=32)
    interrupt_max_audio_bytes: int = Field(6_000_000, ge=100_000, le=12_000_000)
    interrupt_max_audio_seconds: int = Field(60, ge=1, le=120)
    interrupt_source_characters: int = Field(24000, ge=2000, le=60000)
    interrupt_provider_timeout_seconds: int = Field(45, ge=5, le=120)

    # C1/C2 use bounded local checks and observed provider calls, never synthetic inference.
    operations_refresh_seconds: float = Field(10, ge=5, le=60, allow_inf_nan=False)
    operations_probe_timeout_seconds: int = Field(2, ge=1, le=5)
    operations_provider_ttl_seconds: int = Field(900, ge=60, le=3600)
    operations_worker_ttl_seconds: int = Field(150, ge=30, le=600)
    operations_kill_switch: bool = False
    operations_kill_switch_courses: set[str] = Field(default_factory=set)
    operations_disabled_features: set[Literal["answers", "stt", "tts", "generation"]] = Field(default_factory=set)
    operations_course_disabled_features: dict[
        str, set[Literal["answers", "stt", "tts", "generation"]]
    ] = Field(default_factory=dict)

    @model_validator(mode="after")
    def operations_courses_are_bounded(self):
        courses = self.operations_kill_switch_courses | self.operations_course_disabled_features.keys()
        if len(courses) > 1000 or any(not key.strip() or key != key.strip() or len(key) > 120 for key in courses):
            raise ValueError("operations course controls require up to 1000 nonempty course IDs of at most 120 characters")
        return self

    @property
    def project_root(self) -> Path:
        return self.data_dir

    @property
    def pipeline_signature(self) -> str:
        import hashlib
        import json

        keys = (
            "embedding_model_id",
            "embedding_dimension",
            "vision_model_id",
            "math_vision_model_id",
            "fidelity_vision_model_id",
            "enable_ocr",
            "enable_formula_enrichment",
            "describe_images",
            "chunk_max_tokens",
            "chunk_overlap_tokens",
        )
        digest = hashlib.sha256(
            json.dumps({k: getattr(self, k) for k in keys}, sort_keys=True).encode()
        ).hexdigest()
        return f"{self.pipeline_version}-{digest[:12]}"

    @property
    def lesson_signature(self) -> str:
        import hashlib
        identity = ":".join((self.lesson_pipeline_version, self.lesson_generation_model_id,
                             self.lesson_review_model_id, self.lesson_visual_model_id, self.polly_engine))
        return f"{self.lesson_pipeline_version}-{hashlib.sha256(identity.encode()).hexdigest()[:12]}"

    @property
    def lesson_v2_signature(self) -> str:
        import hashlib
        import json
        payload = {"base": self.lesson_signature, "levels": self.lesson_level_profiles,
                   'level_order': list(self.lesson_level_profiles),
                   "plan_level": self.lesson_plan_level, "batch_units": self.lesson_alignment_batch_units}
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:12]
        return f"{self.lesson_v2_pipeline_version}-{digest}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
