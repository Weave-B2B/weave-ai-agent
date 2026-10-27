"""Weave 에이전트 공통 입출력 스키마 v0.3 (AI공통기준 v0.3 · 팀 공통 계약)

이전 초안 두 개(해석 규칙·데이터셋 중심안, 계약·운영 중심안)를 합친 버전.

원칙
- 공유 필드는 엄격하게: 정의되지 않은 필드는 거부(extra="forbid"). 디버그 정보는 meta.debug 한 곳에만.
- 각 에이전트는 자기 출력만 만든다. 앞 단계 결과·PM 검토는 고치지 않고 새 결과를 만든다.
- ID(result_id, review_id 등)는 호출부·저장 계층이 발급한다. LLM이 실행 ID를 만들지 않는다.
- 금액은 calc_cost만 채운다. LLM 출력에 금액이 있으면 검증 실패.
- 모름은 null, 없음은 [], 0은 실제 0.
"""
from __future__ import annotations
from typing import Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = "0.3.0"

# ================= 열거형 =================
Stage = Literal["agent1", "agent2", "agent3"]
ItemType = Literal["function", "constraint", "deadline", "dependency", "question"]
Intent = Literal["change_request", "estimate_only", "confirmation", "defect", "issue_report",
                 "status_check", "constraint", "estimate_request"]
ScopeLabel = Literal["in_scope", "out_of_scope", "unclear", "approved_change"]
EvidenceRole = Literal["basis", "policy", "superseding", "context"]
Role = Literal["PM", "UX", "FE", "BE", "QA"]

A1Status = Literal["READY", "NEEDS_INPUT", "ERROR"]
A2Status = Literal["READY_FOR_REVIEW", "NEEDS_INPUT", "ERROR"]
A3Status = Literal["IMPACT_DRAFT_READY", "NEEDS_INPUT", "WAITING_REVIEW", "NOT_REQUIRED", "ERROR"]
RunStatus = Literal["QUEUED", "RUNNING", "WAITING_INPUT", "WAITING_PM_REVIEW", "COMPLETED", "FAILED"]
ErrorCode = Literal["INVALID_INPUT", "UPSTREAM_NOT_READY", "VERSION_CONFLICT", "RETRIEVAL_UNAVAILABLE",
                    "MODEL_TIMEOUT", "TOOL_TIMEOUT", "TOOL_FAILED", "OUTPUT_INVALID", "INTERNAL_ERROR"]

ESTIMABLE_INTENTS = {"change_request", "estimate_only"}


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ================= 공통 =================
class Context(_M):
    """모든 입출력에 붙는 실행 식별자 (호출부가 채움)"""
    project_id: str = Field(description="프로젝트 ID", examples=["P-COM-101"])
    case_id: str = Field(description="요청 ID (request_id)", examples=["CR-A-001"])
    request_revision: int = Field(1, ge=1, description="원문이 수정될 때만 증가. PM 답변 추가로는 유지")
    baseline_id: Optional[str] = Field(None, description="판단 기준선 (승인 문서 묶음)", examples=["P-COM-101:BL-2"])
    baseline_as_of: Optional[str] = Field(None, description="기준선 적용 시점 YYYY-MM-DD. '최신 파일'이 아니라 이 시점의 승인 문서")
    analysis_id: Optional[str] = Field(None, description="원문 revision·기준선 하나에 대한 분석 흐름 ID. 둘 중 하나가 바뀌면 새 ID")
    scenario_id: Optional[str] = Field(None, description="테스트 사례 ID. 운영 입력은 null")


class Meta(_M):
    """단계 실행 결과의 출처 정보 (호출부가 ID 발급, 에이전트가 모델·지연 기록)"""
    schema_version: str = Field(SCHEMA_VERSION, description="이 스키마 버전")
    result_id: Optional[str] = Field(None, description="이 결과의 불변 ID. 재시도도 새 ID")
    result_revision: int = Field(1, ge=1, description="같은 analysis·단계 안에서 결과 순번")
    created_at: Optional[str] = Field(None, description="ISO 8601 + offset")
    upstream_result_ids: list[str] = Field([], description="입력으로 쓴 앞 단계 결과·PM 검토 ID")
    agent_version: str = Field("0.1.0", description="담당자가 관리하는 에이전트 버전")
    model: Optional[str] = Field(None, description="사용 모델")
    latency_ms: Optional[int] = Field(None, ge=0)
    cost_usd: Optional[float] = Field(None, ge=0)
    trace_id: Optional[str] = Field(None, description="추적 도구 ID")
    debug: dict = Field({}, description="담당자 자유 디버그 정보 (평가·다음 단계에서 무시)")


class AgentError(_M):
    """기술 실패만. 판단 보류·근거 없음·산정 불가는 오류가 아니다"""
    code: ErrorCode
    message: str = Field(description="토큰·비밀·문서 전문을 넣지 않는다")
    stage: Optional[Stage] = None
    retryable: bool = False


class Span(_M):
    """원문 위치. Unicode 코드포인트, 0부터, 끝 미포함. message_text[start:end] == text"""
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    text: str = Field(description="해당 구간 원문 그대로")


class Evidence(_M):
    doc_id: str = Field(examples=["FSP-A-001-v1.0"])
    doc_version: str = Field(examples=["1.0"])
    section_id: str = Field(examples=["F-11"])
    page: Optional[int] = Field(None, ge=1, description="PDF 1부터. HTML은 null")
    line_start: Optional[int] = None
    line_end: Optional[int] = None
    locator: Optional[str] = Field(None, description="HTML 앵커 등 안정적인 위치")
    extraction_version: str = Field("pdftotext-layout-v1", description="본문 추출 버전. 바뀌면 인용 재검증")
    quote: str = Field(description="섹션 원문 부분 문자열 그대로 (공백 차이만 허용)")
    role: EvidenceRole = "basis"
    match_type: Literal["direct", "indirect", "not_mentioned"] = "direct"
    verified: Optional[bool] = Field(None, description="인용 존재 검증 결과 — 코드가 채움")


class Item(_M):
    item_id: str = Field(description="{case_id}-{n}. 같은 의미면 재실행해도 유지", examples=["CR-A-001-1"])
    case_id: str
    display_no: str = Field(examples=["R1"])
    item_type: ItemType
    intent: Intent
    text: str = Field(description="정규화한 항목 문장")
    spans: list[Span] = Field(description="최초 요청 원문 기준 위치. 원문에 없으면 [] + inferred")
    source: Literal["ai", "pm", "gold"] = "ai"
    feature_candidates: list[str] = Field([], description="'doc_id:section_id'")
    inferred: bool = Field(False, description="원문에 없는 추정 항목")
    gold_origin: Optional[str] = Field(None, description="데이터셋 전용 — 정답 출처·검수 상태")


class Question(_M):
    question_id: str = Field(examples=["CR-A-001-Q1"])
    case_id: str
    stage: Stage = Field(description="질문을 만든 단계")
    raised_by: Literal["agent", "pm"] = "agent"
    item_ids: list[str] = []
    field: Optional[str] = Field(None, description="빠진 정보 이름 (예: photo_limit, open_date)")
    text: str
    reason: str = Field(description="무엇이 왜 빠졌나")
    basis_refs: list[str] = Field([], description="근거 참조: 원문 span·문서 조항·도구 missing 필드")
    target: Literal["pm", "client"]
    blocking: bool = Field(False, description="true면 답변 전까지 다음 단계로 가지 않음")
    status: Literal["open", "answered", "resolved", "dismissed"] = Field("open", description="resolved는 재검토 후에만")
    gold_origin: Optional[str] = None


class Answer(_M):
    """질문 답변 이벤트. 계약 변경 승인과 다르다 (승인은 기준선에 반영돼야 함)"""
    answer_id: str
    question_id: str
    text: str
    answered_by: Literal["pm", "client"]
    answered_at: str
    source_ref: Optional[str] = Field(None, description="메일 ID 등 출처")
    verified: bool = False


# ================= ① 요청 구조화 + 질문 관리 =================
class Agent1Input(_M):
    context: Context
    channel: Literal["email", "manual"] = "email"
    channel_src: Optional[str] = Field(None, description="원본 채널 값 (데이터셋 전용)")
    received_at: str
    sender_role: str
    sender_name: Optional[str] = None
    subject: Optional[str] = None
    message_text: str = Field(description="수신 원문 그대로. span 기준")
    pm_notes: list[dict] = []
    answers: list[Answer] = Field([], description="재실행 시 이전 답변")


class _Out(_M):
    context: Context
    meta: Meta = Meta()
    questions: list[Question] = []
    error: Optional[AgentError] = Field(None, description="정상·보류는 null, 기술 실패만 객체")

    def _status_rules(self, ok: str):
        blocking = [q for q in self.questions if q.blocking and q.status == "open"]
        st = self.status  # type: ignore[attr-defined]
        if st == "ERROR" and self.error is None: raise ValueError("ERROR에는 error 필요")
        if st != "ERROR" and self.error is not None: raise ValueError("error가 있으면 status=ERROR")
        if st == ok and blocking: raise ValueError(f"{ok}에는 열린 blocking 질문이 없어야 함")
        if st == "NEEDS_INPUT" and not blocking: raise ValueError("NEEDS_INPUT에는 blocking 질문 1개 이상")


class Agent1Output(_Out):
    status: A1Status
    items: list[Item]
    duplicates: list[dict] = Field([], description="{cr_id, similarity, reason} — MVP 이후")

    @model_validator(mode="after")
    def _chk(self):
        self._status_rules("READY")
        ids = {i.item_id for i in self.items}
        for q in self.questions:
            if q.stage != "agent1": raise ValueError("① 질문은 stage=agent1")
            if not set(q.item_ids) <= ids: raise ValueError(f"{q.question_id}: 없는 항목 참조")
        return self


class AnswerAnalysisInput(_M):
    context: Context
    question: Question
    answer: Answer


class AnswerAnalysisOutput(_M):
    context: Context
    meta: Meta = Meta()
    question_id: str
    resolved: bool = Field(description="답변으로 질문이 해소됐는가 (업로드만으로 자동 해소 아님)")
    rerun_from: Optional[Stage] = Field(description="null = 재실행 불필요")
    affected_item_ids: list[str] = []
    summary: str = ""
    error: Optional[AgentError] = None


# ================= ② 근거 검색 · 범위 판단 =================
class Agent2Input(_M):
    context: Context = Field(description="baseline_id 필수")
    message_text: str
    a1_result_id: Optional[str] = None
    items: list[Item] = Field(description="item_type == function 만")
    constraints: list[Item] = Field([], description="참고용 비기능 항목")
    answers: list[Answer] = []


class Assessment(_M):
    item_id: str
    label: ScopeLabel
    evidence: list[Evidence]
    rationale: str
    evidence_level: Optional[Literal["strong", "medium", "none", "interpretation_needed"]] = None
    missing_info: list[str] = []
    uncertainties: list[str] = []
    approved_change_ref: Optional[str] = Field(None, description="approved_change일 때 변경합의서 ID — 재청구 금지")
    gold_ref: Optional[dict | str] = Field(None, description="데이터셋 전용 — PD 원본 참조")


class Agent2Output(_Out):
    status: A2Status
    assessments: list[Assessment]

    @model_validator(mode="after")
    def _chk(self):
        self._status_rules("READY_FOR_REVIEW")
        asked = {i for q in self.questions for i in q.item_ids}
        for q in self.questions:
            if q.stage != "agent2": raise ValueError("② 질문은 stage=agent2")
        for a in self.assessments:
            if a.label == "unclear" and not (a.missing_info or a.item_id in asked):
                raise ValueError(f"{a.item_id}: unclear는 missing_info 또는 질문 필요")
            if a.label != "unclear" and not a.evidence:
                raise ValueError(f"{a.item_id}: {a.label}에는 근거 필요 (근거 없음 = unclear)")
            if a.label == "approved_change" and not a.approved_change_ref:
                raise ValueError(f"{a.item_id}: approved_change에는 변경합의서 참조 필요")
        return self


# ================= PM 검토 (별도 불변 기록) =================
class ItemReview(_M):
    item_id: str
    ai_label: ScopeLabel = Field(description="검토 대상 ② 결과의 라벨 (복사)")
    pm_label: ScopeLabel = Field(description="PM 확정 라벨")
    estimate: bool = Field(description="③ 산정 여부. 기본값은 suggest_estimate()")
    reason: str = Field("", description="ai_label과 다르면 필수")
    evidence_refs: list[str] = Field([], description="'doc_id:section_id'")


class PMReview(_M):
    review_id: str
    review_revision: int = 1
    target_result_id: Optional[str] = Field(description="검토한 ② 결과 ID — 다르면 VERSION_CONFLICT")
    reviewer: str
    reviewed_at: str
    status: Literal["PENDING", "REVIEWED"]
    items: list[ItemReview]

    @model_validator(mode="after")
    def _chk(self):
        for r in self.items:
            if r.pm_label != r.ai_label and not r.reason: raise ValueError(f"{r.item_id}: 라벨 변경 사유 필요")
            if r.estimate and r.pm_label != "out_of_scope": raise ValueError(f"{r.item_id}: 범위 밖만 산정")
            if r.pm_label == "unclear" and self.status == "REVIEWED" and r.estimate:
                raise ValueError("unclear는 산정할 수 없음")
        return self


# ================= ③ 영향 산정 + 초안 =================
class Agent3Input(_M):
    context: Context
    rate_card_id: str = Field(examples=["RC-2026H2-v3"])
    received_at: str
    items: list[Item] = Field(description="① 확정 function 항목")
    a2_result: Agent2Output
    pm_review: PMReview
    constraints: list[Item] = Field([], description="deadline·constraint·dependency 항목")
    answers: list[Answer] = []

    def targets(self) -> list[Item]:
        """산정 대상 = PM이 estimate=true로 고른 항목"""
        sel = {r.item_id for r in self.pm_review.items if r.estimate}
        return [i for i in self.items if i.item_id in sel]


class Line(_M):
    task: str
    item_ids: list[str]
    role: Role
    md: float = Field(ge=0, description="0.5 단위. 0 허용")
    assumption: str = ""


class Schedule(_M):
    business_days: Optional[float] = Field(None, description="null = 산정 불가 (0일 아님). MD를 달력일로 바꾸지 않음")
    basis: list[str] = []
    assumptions: list[str] = []
    reason: str = ""


class Totals(_M):
    """calc_cost 결과 — LLM이 쓰지 않는다"""
    md: float
    md_by_role: dict[str, float]
    cost: int = Field(description="KRW 원가, VAT 별도")
    price: int = Field(description="KRW 판매 산정가, VAT 별도")
    margin: Optional[float] = Field(description="(판매가-원가)/판매가. 판매가 0이면 null")
    currency: Literal["KRW"] = "KRW"
    vat_included: bool = False
    rate_card_id: str


class Estimate(_M):
    item_ids: list[str]
    status: Literal["draft", "not_estimable"]
    lines: list[Line] = []
    totals: Optional[Totals] = Field(None, description="calc_cost만 채움")
    totals_source: Optional[Literal["calc_cost"]] = None
    schedule: Schedule
    rate_card_id: str
    gold_origin: Optional[str] = None

    @model_validator(mode="after")
    def _chk(self):
        if self.status == "not_estimable" and (self.lines or not self.schedule.reason):
            raise ValueError("not_estimable: lines는 비우고 schedule.reason 필수")
        if self.status == "draft" and not self.lines: raise ValueError("draft: lines 필수")
        for l in self.lines:
            if (l.md * 2) % 1: raise ValueError(f"MD {l.md}는 0.5 단위가 아님")
        if self.totals is not None and self.totals_source != "calc_cost":
            raise ValueError("totals는 calc_cost 결과만")
        return self


class Draft(_M):
    """협의용 초안 — 결과에서 복사해 템플릿을 채움. 금액·근거를 새로 쓰지 않음"""
    summary: str
    scope: str = Field(description="범위 판단과 근거 (② 결과·PM 검토 복사)")
    impact: str = Field(description="작업·공수·일정 (③ 결과 복사)")
    cost: Optional[str] = Field(None, description="calc_cost 결과 표기만")
    open_questions: list[str] = []
    source_result_ids: list[str] = []


class Agent3Output(_Out):
    status: A3Status
    estimates: list[Estimate]
    draft: Optional[Draft] = None
    review_required: Literal[True] = True
    billing_authorized: Literal[False] = False

    @model_validator(mode="after")
    def _chk(self):
        self._status_rules("IMPACT_DRAFT_READY")
        for q in self.questions:
            if q.stage != "agent3": raise ValueError("③ 질문은 stage=agent3")
        if self.status == "NOT_REQUIRED" and self.estimates: raise ValueError("NOT_REQUIRED는 estimates 비움")
        return self


# ================= 오케스트레이션 상태 =================
class WorkflowState(_M):
    context: Context
    status: RunStatus = "QUEUED"
    stage: Optional[Literal["agent1", "pm_items", "agent2", "pm_review", "agent3", "lead_review"]] = None
    state_revision: int = Field(0, ge=0, description="재개 시 비교 — 오래된 화면의 덮어쓰기 차단")
    a1_result_id: Optional[str] = None
    a2_result_id: Optional[str] = None
    pm_review_id: Optional[str] = None
    a3_result_id: Optional[str] = None
    stale_result_ids: list[str] = Field([], description="무효화된 결과 (삭제하지 않고 보존)")
    open_question_ids: list[str] = []
    rerun_from: Optional[Stage] = None
    error: Optional[AgentError] = None
    history: list[dict] = Field([], description="{at, event, by, state_revision}")


# ================= 규칙 함수 (오케스트레이션·③·평가 공용) =================
def suggest_estimate(pm_label: str, item: Item) -> bool:
    """PM 화면의 산정 체크 기본값. PM이 바꿀 수 있다."""
    return pm_label == "out_of_scope" and item.intent in ESTIMABLE_INTENTS


RERUN_TABLE = {   # 변경 종류 → (재실행 시작, 무효화 대상)
    "request_changed":        ("agent1", ["a1", "a2", "pm_review", "a3"]),
    "baseline_or_evidence":   ("agent2", ["a2", "pm_review", "a3"]),
    "pm_review_changed":      ("agent3", ["a3"]),
    "estimate_input_changed": ("agent3", ["a3"]),
}

MODELS = {
    "agent1_input": Agent1Input, "agent1_output": Agent1Output,
    "answer_input": AnswerAnalysisInput, "answer_output": AnswerAnalysisOutput,
    "agent2_input": Agent2Input, "agent2_output": Agent2Output,
    "pm_review": PMReview,
    "agent3_input": Agent3Input, "agent3_output": Agent3Output,
    "workflow_state": WorkflowState,
}
