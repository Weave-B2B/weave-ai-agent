"""Agent 3 tools contract for a trusted orchestration caller (no fabricated PM review)."""
from typing import Literal

from pydantic import Field, model_validator

from schema.schemas import Agent3Input, PMReview, Line, Estimate, Agent3Output
from pydantic import BaseModel, ConfigDict


class WorkbookReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_version_id: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class ProvisionalAgent3Input(Agent3Input):
    mode: Literal["provisional"] = "provisional"
    pm_review: None = None
    target_item_ids: list[str] = Field(default_factory=list)
    conditional_item_ids: list[str] = Field(default_factory=list)
    customer_questions: list[str] = Field(default_factory=list)
    workbook_source_pinned: bool = False
    workbook_source: WorkbookReference | None = None
    quote_links: dict[str, list[str]] = Field(default_factory=dict,
        description="estimate_request item_id -> 견적 대상 item_ids. 오케스트레이터 에이전트가 Agent 1 결과로 채운다.")

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


class ScheduleHint(BaseModel):
    """작업 행의 일정 배치 제안. 모델이 제안하고 엑셀 이름과 대조한 값만 남긴다. 계산은 Backend가 한다.

    assignee: 새 작업 담당자(엑셀 '사람과 근무일'의 담당자). after/before: 이 작업의 선행·후속 기존 작업명.
    extends_task: 새 작업이 아니라 기존 작업의 남은 시간이 늘어나는 경우 그 작업명(담당자는 기존 작업 담당자).
    """
    model_config = ConfigDict(extra="forbid")
    assignee: str | None = None
    after: list[str] = Field(default_factory=list)
    before: list[str] = Field(default_factory=list)
    extends_task: str | None = None


class ProvisionalLine(Line):
    shared_work_key: str | None = Field(None, min_length=1, max_length=120)
    schedule_hint: ScheduleHint | None = None


class ProvisionalEstimate(Estimate):
    lines: list[ProvisionalLine] = Field(default_factory=list)


class PricingSnapshot(BaseModel):
    """계산식 v1 요청 전체 금액 스냅샷. 백엔드 공식 계산기만 채우고 LLM은 쓰지 않는다.

    금액은 KRW·VAT 별도. supply_amount가 견적액(공급가액)이며 비율은 0.3 형태다.
    기존 Totals(산정 그룹 인건비)와 달리 경비·할인·예비비·부가세를 요청 전체에 한 번만 적용한다.
    """
    model_config = ConfigDict(extra="allow")
    formula_version: str
    rate_card_id: str | None = None
    hours_per_md: str
    labor_price: int
    labor_cost: int
    direct_cost: int | None
    subtotal: int
    discount_amount: int
    contingency_amount: int
    supply_amount: int
    vat_amount: int
    total_with_vat: int
    profit: int | None
    profit_ratio: str | None = None
    markup_ratio: str | None = None
    policy_status: str
    expenses_state: str
    provisional: bool
    provisional_reasons: list[str] = Field(default_factory=list)
    calculated_at: str | None = None
    calculated_by: str | None = None
    input_hash: str | None = None
    inputs: dict | None = None


class ProvisionalAgent3Output(Agent3Output):
    estimates: list[ProvisionalEstimate]
    needs: list[Agent3Need] = Field(default_factory=list)
    pricing: PricingSnapshot | None = Field(None, description="요청 전체 계산식 v1 결과. 미산정·단가 누락이 있으면 null")
    impact_workbook: dict | None = Field(None, description="승인된 일정·공수 자료의 버전·해시와 구조화 입력. 계약 근거 아님")
    schedule_detail: dict | None = Field(None, description="소요 영업일(37)·남은 영업일(42). 엑셀이 있으면 plan_detail에 완료일·일정 영향(38~41)")
    plan_detail: dict | None = Field(None, description="schedule-v1 일정 계산 결과(Backend) 또는 미계산 사유. 엑셀 자료가 없으면 null")

    def _status_rules(self, ok):
        blocked = any(n.blocking for n in self.needs)
        if self.status == ok and blocked:
            raise ValueError("ready output cannot have blocking tool needs")
        if self.status == "NEEDS_INPUT" and blocked:
            if self.error is not None:
                raise ValueError("technical failures must use ERROR")
            return
        super()._status_rules(ok)
