from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
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
    enable_ocr: bool = True
    enable_formula_enrichment: bool = True
    describe_images: bool = True
    max_pdf_size_mb: int = Field(100, ge=1, le=500)
    max_pdf_pages: int = Field(500, ge=1, le=5000)
    chunk_max_tokens: int = Field(600, ge=100, le=2000)
    chunk_overlap_tokens: int = Field(60, ge=0)
    pipeline_version: str = "pdf-ingestion-v1.3"
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


@lru_cache
def get_settings() -> Settings:
    return Settings()
