"""Untrusted impact proposals; application code owns IDs, totals and schedules."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from schema.schemas import Role


class _Proposal(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ProposedLine(_Proposal):
    shared_work_key: str | None = None
    task: str = Field(min_length=1)
    item_numbers: list[int] = Field(min_length=1)
    role: Role
    rule_id: str = ""
    md: float | None = Field(default=None, ge=0)  # Legacy model value is never used for calculation.
    assumption: str = ""
    # 일정 계산용 제안(operational_schedule이 있을 때만). 이름은 엑셀의 담당자·기존 작업명을 그대로 쓴다.
    assignee: str | None = Field(default=None, max_length=120)
    after: list[str] = Field(default_factory=list, max_length=20)
    before: list[str] = Field(default_factory=list, max_length=20)
    extends_task: str | None = Field(default=None, max_length=200)

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
    choices: list[str] = Field(default_factory=list, max_length=3, description=(
        "답변자가 고를 구체 선택지 0~3개. '기타·직접 입력'·'모름'은 화면이 붙이므로 넣지 않는다"))
    target: Literal["pm", "client"] = "pm"
    blocking: bool = False


class ProposedNeed(_Proposal):
    kind: Literal["more_evidence", "client_input", "missing_standard"]
    item_numbers: list[int] = Field(min_length=1)
    topic: str = Field(min_length=1)
    reason: str = ""
    choices: list[str] = Field(default_factory=list, max_length=3, description=(
        "답변자가 고를 구체 선택지 0~3개. '기타·직접 입력'·'모름'은 화면이 붙이므로 넣지 않는다"))
    target: Literal["pm", "client"] | None = None


class Agent3ImpactDraft(_Proposal):
    estimates: list[ProposedEstimate] = Field(min_length=1)
    questions: list[ProposedQuestion] = Field(default_factory=list, max_length=3)
    needs: list[ProposedNeed] = Field(default_factory=list, max_length=5)
