"""Untrusted impact proposals; application code owns IDs, totals and schedules."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from schema.schemas import Role


class _Proposal(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ProposedLine(_Proposal):
    task: str = Field(min_length=1)
    item_numbers: list[int] = Field(min_length=1)
    role: Role
    rule_id: str = ""
    md: float | None = Field(default=None, ge=0)  # Legacy model value is never used for calculation.
    assumption: str = ""

    @model_validator(mode="after")
    def check_md(self):
        if self.md is not None and (self.md * 2) % 1:
            raise ValueError("MD must use 0.5 steps")
        return self


class ProposedEstimate(_Proposal):
    item_numbers: list[int] = Field(min_length=1)
    status: Literal["draft", "not_estimable"]
    lines: list[ProposedLine] = Field(default_factory=list)
    reason: str = ""

    @model_validator(mode="after")
    def check_lines(self):
        if self.status == "draft" and not self.lines:
            raise ValueError("draft needs task lines")
        if self.status == "not_estimable" and (self.lines or not self.reason.strip()):
            raise ValueError("not_estimable needs a reason and no lines")
        return self


class ProposedQuestion(_Proposal):
    item_numbers: list[int] = Field(min_length=1)
    field: str | None = None
    text: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    target: Literal["pm", "client"] = "pm"
    blocking: bool = False


class Agent3ImpactDraft(_Proposal):
    estimates: list[ProposedEstimate] = Field(min_length=1)
    questions: list[ProposedQuestion] = Field(default_factory=list, max_length=3)
