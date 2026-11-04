"""Agent 3 tools contract for a trusted orchestration caller (no fabricated PM review)."""
from typing import Literal

from pydantic import Field, model_validator

from schema.schemas import Agent3Input, PMReview, Line, Estimate, Agent3Output
from pydantic import BaseModel, ConfigDict


class ProvisionalAgent3Input(Agent3Input):
    mode: Literal["provisional"] = "provisional"
    pm_review: None = None
    target_item_ids: list[str] = Field(default_factory=list)
    conditional_item_ids: list[str] = Field(default_factory=list)
    customer_questions: list[str] = Field(default_factory=list)
    quote_links: dict[str, list[str]] = Field(default_factory=dict,
        description="estimate_request item_id -> 견적 대상 item_ids. 매니저가 Agent 1 결과로 채운다.")

    @model_validator(mode="after")
    def unique_targets(self):
        ids = [i.item_id for i in self.items]
        if len(ids) != len(set(ids)) or len(self.target_item_ids) != len(set(self.target_item_ids)):
            raise ValueError("duplicate item IDs")
        if not set(self.target_item_ids) <= set(ids) or not set(self.conditional_item_ids) <= set(ids):
            raise ValueError("unknown target or conditional item")
        intents = {i.item_id: i.intent for i in self.items}
        for quote, linked in self.quote_links.items():
            if intents.get(quote) != "estimate_request":
                raise ValueError("quote link source must be an estimate_request item")
            if not linked or len(linked) != len(set(linked)) or quote in linked:
                raise ValueError("quote link needs distinct target items")
            if any(intents.get(i) in (None, "estimate_request") for i in linked):
                raise ValueError("quote link must point to known non-quote items")
        return self

    def quote_targets(self) -> set[str]:
        """Items whose price was explicitly requested by a linked estimate_request."""
        return {i for linked in self.quote_links.values() for i in linked}

    def targets(self):
        return [i for i in self.items if i.item_id in self.target_item_ids]


def upstream_ids(inp: Agent3Input | ProvisionalAgent3Input) -> list[str]:
    return [x for x in [inp.a2_result.meta.result_id,
        inp.pm_review.review_id if isinstance(inp.pm_review, PMReview) else None] if x]


class Agent3Need(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["more_evidence", "client_input", "missing_standard", "not_required"]
    item_ids: list[str] = Field(min_length=1)
    topic: str = Field(min_length=1)
    target: Literal["agent2", "pm", "client", "none"] | None = None
    source: Literal["agent2", "agent3", "server"] = "server"
    question_id: str | None = None
    blocking: bool = True

    @model_validator(mode="after")
    def route(self):
        if self.kind == "more_evidence":
            self.target = "agent2"
        elif self.kind == "missing_standard":
            self.target = "pm"
        elif self.kind == "not_required":
            self.target, self.blocking = "none", False
        elif self.target is None:
            self.target = "pm"
        elif self.target not in ("pm", "client"):
            raise ValueError("input questions must target PM or customer")
        return self


class ProvisionalLine(Line):
    shared_work_key: str | None = Field(None, min_length=1, max_length=120)


class ProvisionalEstimate(Estimate):
    lines: list[ProvisionalLine] = Field(default_factory=list)


class ProvisionalAgent3Output(Agent3Output):
    estimates: list[ProvisionalEstimate]
    needs: list[Agent3Need] = Field(default_factory=list)

    def _status_rules(self, ok):
        blocked = any(n.blocking for n in self.needs)
        if self.status == ok and blocked:
            raise ValueError("ready output cannot have blocking tool needs")
        if self.status == "NEEDS_INPUT" and blocked:
            if self.error is not None:
                raise ValueError("technical failures must use ERROR")
            return
        super()._status_rules(ok)
