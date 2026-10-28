"""V3.1 host-bound evidence and small, indexed corrections."""

from pydantic import BaseModel, ConfigDict, Field, StrictBool


class EvidenceText(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=16000)
    evidence_ids: list[str] = Field(min_length=1, max_length=40)


class EvidenceDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    paragraphs: list[EvidenceText] = Field(min_length=1, max_length=200)


class EvidenceUnit(EvidenceText):
    content_unit_id: str = Field(min_length=1, max_length=100)


class EvidenceUnits(BaseModel):
    model_config = ConfigDict(extra="forbid")
    units: list[EvidenceUnit] = Field(min_length=1, max_length=8)


class LevelCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    suitable: StrictBool
    reason: str = Field(min_length=1, max_length=1500)


class SpeechTaskCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preserved: StrictBool
    reason: str = Field(min_length=1, max_length=1500)
