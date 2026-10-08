"""Phase-one input contracts for invented portfolio identifiers and human review."""
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DemoPatient(BaseModel):
    model_config = ConfigDict(extra="forbid")

    patient_id: str = Field(pattern=r"^PAT-[0-9]{6}$")
    date_of_birth: date
    sex_at_registration: Literal["female", "male", "other", "unknown"]
    area_group: Literal["urban", "semi-rural", "unknown"]

    @field_validator("date_of_birth")
    @classmethod
    def reject_future_birthdates(cls, value: date) -> date:
        if value > date.today():
            raise ValueError("birth date cannot be in the future")
        return value


class ClinicalApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reviewer_id: str = Field(pattern=r"^STF-[0-9]{4}$")
    reviewer_role: Literal["GP", "NURSE_PRACTITIONER", "PRACTICE_NURSE"]
    decision: Literal["approved", "rejected"]
    reviewed_at: datetime

    @field_validator("reviewed_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("reviewed_at must have timezone information")
        return value