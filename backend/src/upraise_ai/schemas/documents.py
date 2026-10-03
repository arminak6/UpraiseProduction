import re
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DocumentInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    course_id: str = Field(min_length=1, max_length=120)
    activity_id: str = Field(min_length=1, max_length=120)
    language: str = Field(min_length=2, max_length=35)
    source_ref: str = Field(min_length=1, max_length=200)
    content_hash: str = Field(min_length=1, max_length=200)
    version: str | None = Field(None, min_length=1, max_length=120)

    @field_validator("language")
    @classmethod
    def valid_language(cls, value: str) -> str:
        if not re.fullmatch(r"[a-zA-Z]{2,3}(?:-[a-zA-Z0-9]{2,8})*", value):
            raise ValueError("language must be a source-language tag, e.g. it or en-GB")
        return value.lower()

    @field_validator("course_id", "activity_id", "source_ref", "version")
    @classmethod
    def clean_identifier(cls, value: str | None) -> str | None:
        if value and any(ord(c) < 32 for c in value):
            raise ValueError("control characters are not permitted")
        return value


class UrlDocumentInput(DocumentInput):
    moodle_url: str = Field(min_length=1, max_length=8192)


class IngestionMetadata(DocumentInput):
    source_filename: str = Field(min_length=1, max_length=300)

    @property
    def document_title(self) -> str:
        return self.source_filename


class AcceptedDocument(BaseModel):
    document_id: str


class DocumentSummary(BaseModel):
    document_id: str
    course_id: str
    activity_id: str
    language: str
    source_ref: str
    content_hash: str
    version: str | None
    filename: str
    status: str
    chunk_count: int
    created_at: datetime
    updated_at: datetime


class ElementType(str, Enum):
    TITLE = "title"
    HEADING = "heading"
    TEXT = "text"
    LIST_ITEM = "list_item"
    TABLE = "table"
    FIGURE = "figure"
    FORMULA = "formula"
    CODE = "code"
    CAPTION = "caption"
    HEADER = "header"
    FOOTER = "footer"
    OTHER = "other"


class BoundingBox(BaseModel):
    left: float
    top: float
    right: float
    bottom: float
    coordinate_origin: str | None = None


class DocumentElement(BaseModel):
    element_id: str
    element_index: int
    element_type: ElementType
    original_text: str = ""
    structured_content: str | None = None
    derived_description: str | None = None
    caption: str | None = None
    page_number: int | None = None
    page_numbers: list[int] = Field(default_factory=list)
    bounding_box: BoundingBox | None = None
    heading_path: list[str] = Field(default_factory=list)
    asset_path: str | None = None
    source_reference: str | None = None
    parser_label: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_retrievable(self) -> bool:
        return bool(
            self.original_text.strip() or self.structured_content or self.derived_description or self.caption
        )


class Chunk(BaseModel):
    chunk_id: str
    chunk_index: int
    document_id: str
    document_version_id: str
    source_hash: str
    course_id: str
    activity_id: str
    language: str
    source_ref: str
    version: str | None
    document_title: str
    text: str
    embedding_text: str
    token_count: int
    page_numbers: list[int] = Field(default_factory=list)
    heading_path: list[str] = Field(default_factory=list)
    element_ids: list[str] = Field(default_factory=list)
    element_types: list[ElementType] = Field(default_factory=list)


class ProcessedDocument(BaseModel):
    document_id: str
    document_version_id: str
    source_hash: str
    metadata: IngestionMetadata
    parser_name: str
    parser_version: str
    pipeline_version: str
    embedding_model_id: str
    embedding_dimension: int
    page_count: int
    elements: list[DocumentElement]
    chunks: list[Chunk]
    warnings: list[str]
