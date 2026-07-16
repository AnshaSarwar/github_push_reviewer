"""Pydantic models for structured AI review output."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class Severity(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class Status(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"


FAILING_SEVERITIES: frozenset[Severity] = frozenset({Severity.HIGH, Severity.CRITICAL})


class Issue(BaseModel):
    """A single review finding tied to a path/line in the supplied diff."""

    severity: Severity
    file: str
    line: int | None = None
    message: str
    suggestion: str = ""


class LLMReviewPayload(BaseModel):
    """Schema expected from the model before we recompute status."""

    status: Literal["PASS", "FAIL"] | None = None
    score: int = Field(ge=0, le=100)
    summary: str
    issues: list[Issue] = Field(default_factory=list)

    @field_validator("score", mode="before")
    @classmethod
    def clamp_score(cls, value: object) -> object:
        if isinstance(value, bool):
            raise TypeError("score must be an int")
        return value


class ReviewResult(BaseModel):
    """Final review result returned to callers (CLI / CI)."""

    status: Status
    score: int = Field(ge=0, le=100)
    summary: str
    issues: list[Issue] = Field(default_factory=list)
    infrastructure_failure: bool = False

    def to_public_dict(self) -> dict:
        """Serialize for JSON output / GitHub comments."""
        return self.model_dump(mode="json")


def derive_status(issues: list[Issue]) -> Status:
    """Deterministic gate: any HIGH or CRITICAL issue forces FAIL."""
    for issue in issues:
        if issue.severity in FAILING_SEVERITIES:
            return Status.FAIL
    return Status.PASS


def derive_score(issues: list[Issue], model_score: int) -> int:
    """
    Prefer model score but never allow a high score when failing issues exist.
    """
    status = derive_status(issues)
    if status == Status.FAIL:
        return min(model_score, 49)
    return max(model_score, 50) if issues else max(model_score, 90)


def infrastructure_failure(summary: str) -> ReviewResult:
    """Fail-closed result when the review pipeline itself breaks."""
    return ReviewResult(
        status=Status.FAIL,
        score=0,
        summary=f"review infrastructure failed: {summary}",
        issues=[],
        infrastructure_failure=True,
    )


def finalize_from_llm(payload: LLMReviewPayload) -> ReviewResult:
    """Recompute status from severities; do not trust the model's status field."""
    status = derive_status(payload.issues)
    score = derive_score(payload.issues, payload.score)
    summary = payload.summary.strip() or (
        "No issues reported." if status == Status.PASS else "Issues found in diff."
    )
    return ReviewResult(
        status=status,
        score=score,
        summary=summary,
        issues=payload.issues,
        infrastructure_failure=False,
    )
