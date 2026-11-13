"""Versioned Agent 3 persistence DTO, separate from the shared schema v0.3.

No session checkpoint, model scratchpad or internal runtime object is stored here.
"""
from typing import Literal

from pydantic import Field

from app.agents.agent3.contracts import ProvisionalAgent3Output
from schema.schemas import Agent2Output, _M


class Agent2RunOutput(Agent2Output):
    input_item_hashes: dict[str, str] = Field(default_factory=dict)


class Agent3Source(_M):
    a1_result_id: str
    a2_result_id: str
    answer_ids: list[str] = Field(default_factory=list)
    rate_card_id: str
    quote_links: dict[str, list[str]] = Field(default_factory=dict)
    rate_hash: str
    effort_hash: str
    pm_edit_id: str | None = None


class CustomerDocumentDraft(_M):
    result_id: str = Field(min_length=1)
    impact_result_id: str = Field(min_length=1)
    a2_result_id: str = Field(min_length=1)
    status: Literal["customer_unagreed_draft"] = "customer_unagreed_draft"
    model: str
    subject: str | None = None
    body: str = Field(min_length=1)


class Agent3RunOutput(ProvisionalAgent3Output):
    contract_version: Literal["agent3-run/1"] = "agent3-run/1"
    documents: dict[Literal["email", "agreement", "scope_reply"], CustomerDocumentDraft] = Field(default_factory=dict)
    source: Agent3Source | None = None
