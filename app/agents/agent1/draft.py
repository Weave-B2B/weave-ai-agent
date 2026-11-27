"""Internal structured-output models used to assemble Agent1 results."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from schema.schemas import ChangeKind, Intent, ItemAction, ItemType


class _DraftModel(BaseModel):
    """Reject undeclared fields in LLM-only draft models."""

    model_config = ConfigDict(extra="forbid")


class DraftItem(_DraftModel):
    """Represent one untrusted item draft before IDs and spans are assigned."""

    item_type: ItemType
    intent: Intent
    text: str = Field(min_length=1)
    quotes: list[str] = Field(default_factory=list)
    inferred: bool = False
    conditional_followup: str | None = None
    change_kind: ChangeKind | None = None
    # 회신 분석 전용: existing_items와의 관계. target_item_no는 existing_items 번호(1부터)
    action: ItemAction = "new"
    target_item_no: int | None = None


class DraftQuestion(_DraftModel):
    """Represent one question draft using item numbers instead of generated IDs."""

    item_numbers: list[int] = Field(default_factory=list)
    field: str | None = None
    text: str
    reason: str
    choices: list[str] = Field(default_factory=list, max_length=3, description=(
        "답변자가 고를 구체 선택지 0~3개. '기타·직접 입력'·'모름'은 화면이 붙이므로 넣지 않는다"))
    target: Literal["pm", "client"]
    blocking: bool = False
    quote: str | None = None


class Agent1Draft(_DraftModel):
    """Structured LLM draft to be converted into the shared Agent1 output."""

    items: list[DraftItem]
    questions: list[DraftQuestion] = Field(default_factory=list)
    summary: str | None = None
    title: str | None = None
