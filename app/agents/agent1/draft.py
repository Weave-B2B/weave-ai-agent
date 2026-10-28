"""Internal structured-output models used to assemble Agent1 results."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from schema.schemas import Intent, ItemType


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


class DraftQuestion(_DraftModel):
    """Represent one question draft using item numbers instead of generated IDs."""

    item_numbers: list[int] = Field(default_factory=list)
    field: str | None = None
    text: str
    reason: str
    target: Literal["pm", "client"]
    blocking: bool = False
    quote: str | None = None


class Agent1Draft(_DraftModel):
    """Structured LLM draft to be converted into the shared Agent1 output."""

    items: list[DraftItem]
    questions: list[DraftQuestion] = Field(default_factory=list)
