"""② 근거 검색·범위 판단 에이전트 (담당: 선승규).

흐름: 검색 계획 LLM → search_docs(기준선 필터) → 근거 충분? (부족하면 재검색, 최대 3회)
      → get_section → 판단 LLM → verify_quote(코드) → 가드(apply_guards) → assessment
평가: 라벨 정확도·macro-F1, unclear 정밀도/재현율, Hit@5·MRR, 인용 존재율·지지율, 근거 없는 out_of_scope 0건
"""
import concurrent.futures as cf
import contextvars
import json
import re
import threading
from typing import Literal, Optional

from pydantic import BaseModel, Field

from schema.schemas import (
    AgentError, Agent2Input, Agent2Output, Assessment, EvidenceRole, Evidence, Item, Meta, Question, ScopeLabel,
)

from app.agents.agent2.guards import apply_guards
from app.agents.agent2.prompts import JUDGE_SYSTEM, PLAN_SYSTEM
from app.agents.base import AgentFailure
from app.core.config import settings
from app.kb.loader import baselines, documents, sections
from app.llm.client import LlmClient
from app.tools.doc_tools import get_section, list_baseline_docs, search_docs
from app.tools.errors import ToolInputError

AGENT_VERSION = "a2-llm-0.1.0"
_WS = re.compile(r"\s+")
DocType = Literal["contract", "sow", "functional_spec", "quote", "change_order"]


# ---- LLM 구조화 출력 (내부용. 공통 스키마가 아니다) ----
class SearchPlan(BaseModel):
    queries: list[str] = Field(description="검색어 1~3개")
    doc_types: list[DocType] = Field(description="좁힐 문서 종류. 비우면 전체")


class Cite(BaseModel):
    doc_id: str
    section_id: str
    quote: str
    role: EvidenceRole
    match_type: Literal["direct", "indirect", "not_mentioned"]


class QuestionDraft(BaseModel):
    text: str
    reason: str
    field: Optional[str]
    target: Literal["pm", "client"]
    blocking: bool


class Judgment(BaseModel):
    sufficient: bool
    next_queries: list[str]
    label: ScopeLabel
    evidence: list[Cite]
    rationale: str
    evidence_level: Literal["strong", "medium", "none", "interpretation_needed"]
    missing_info: list[str]
    uncertainties: list[str]
    approved_change_ref: Optional[str]
    question: Optional[QuestionDraft]
    defect_facts: list[str] = Field([], description="issue_report를 in_scope로 볼 때: 결함으로 볼 사실을 요청 원문에서 글자 그대로 인용")


def _facts_in(facts: list[str], message_text: str) -> bool:
    flat = _WS.sub("", message_text)
    return any(f.strip() and _WS.sub("", f) in flat for f in facts)


def _rrf(result_lists: list[list[dict]], k: int = 60, top: int = 5) -> list[tuple[str, str]]:
    """검색어별 결과를 Reciprocal Rank Fusion으로 합친다."""
    score: dict[tuple[str, str], float] = {}
    for res in result_lists:
        for rank, r in enumerate(res, 1):
            key = (r["doc_id"], r["section_id"])
            score[key] = score.get(key, 0.0) + 1 / (k + rank)
    return [key for key, _ in sorted(score.items(), key=lambda x: -x[1])[:top]]


def _candidate(key: tuple[str, str]) -> dict:
    s = sections()[key]
    return {"doc_id": s["doc_id"], "section_id": s["section_id"], "doc_type": s["doc_type"],
            "priority_rank": s["priority_rank"], "heading": s["heading"], "text": s["text"]}


class _Usage:
    """항목들(스레드)의 LLM 호출 지연·토큰·모델을 합친다."""

    def __init__(self):
        self.calls = self.latency_ms = self.input_tokens = self.output_tokens = 0
        self.models: set[str] = set()
        self.traces: dict[str, list[str]] = {}          # 항목별 행동 순서 (도구 사용 에이전트만)
        self.steps: dict[str, list[dict]] = {}          # 항목별 행동 상세 (검색어·결과·읽은 조항) — 트러블슈팅용
        self._lock = threading.Lock()

    def trace(self, item_id: str, actions: list[str], steps: Optional[list[dict]] = None) -> None:
        with self._lock:
            self.traces[item_id] = actions
            if steps is not None:
                self.steps[item_id] = steps

    def add(self, r) -> None:
        with self._lock:
            self._add(r)

    def _add(self, r) -> None:
        self.calls += 1
        self.latency_ms += r.latency_ms
        self.input_tokens += r.usage.input_tokens
        self.output_tokens += r.usage.output_tokens
        self.models.add(r.label)


class LlmAgent2:
    """고정 흐름(워크플로) 구현. 도구 사용 에이전트는 tool_agent.ToolAgent2 — 비교 평가용으로 남긴다."""
    agent_version = AGENT_VERSION
    max_search_rounds = 3
    max_found = 12                                       # 검색으로 찾은 후보 상한 (기본 후보와 별도 몫)

    def __init__(self, llm: LlmClient, use_hints: Optional[bool] = None):
        self.llm = llm
        self.use_hints = settings.agent2_use_hints if use_hints is None else use_hints

    def _ask(self, system: str, user: str, schema, tool_name: str, usage: _Usage):
        try:
            r = self.llm.complete_structured(system=system, user=user, schema=schema, tool_name=tool_name)
        except AgentFailure as e:                        # 공통 클라이언트는 stage를 비워 둔다
            raise AgentFailure(e.error.model_copy(update={"stage": "agent2"})) from None
        usage.add(r)
        return r.parsed

    def run(self, inp: Agent2Input) -> Agent2Output:
        ctx = inp.context
        if not ctx.baseline_id:
            raise ToolInputError("baseline_id 필수")
        baseline = list_baseline_docs(ctx.baseline_id)
        usage = _Usage()
        shared = self._shared_context(inp, baseline)

        def one(item: Item):
            return self._assess(item, inp, shared, usage)

        with cf.ThreadPoolExecutor(max_workers=4) as ex:
            futs = [ex.submit(contextvars.copy_context().run, one, it) for it in inp.items]
            results = [f.result() for f in futs]

        assessments, questions, hints = [], [], {}
        for a, q, h in results:
            assessments.append(a)
            hints[a.item_id] = h
            if q is not None:
                questions.append(q.model_copy(update={"question_id": f"{ctx.case_id}-A2Q{len(questions) + 1}"}))
        status = "NEEDS_INPUT" if any(q.blocking for q in questions) else "READY_FOR_REVIEW"
        out = Agent2Output(
            context=ctx, status=status, assessments=assessments, questions=questions,
            meta=Meta(agent_version=self.agent_version, model=",".join(sorted(usage.models)) or None,
                      latency_ms=usage.latency_ms,
                      upstream_result_ids=[inp.a1_result_id] if inp.a1_result_id else [],
                      debug={"llm_calls": usage.calls, "input_tokens": usage.input_tokens,
                             "output_tokens": usage.output_tokens,
                             **({"actions": usage.traces} if usage.traces else {}),
                             **({"steps": usage.steps} if usage.steps else {})}),
        )
        guarded = apply_guards(out)
        cited, kept = (sum(len(a.evidence) for a in o.assessments) for o in (out, guarded))
        guarded.meta.debug["quotes"] = {"cited": cited, "verified": kept}   # 인용 통과율 = verified / cited
        # 힌트로 넣은 후보와 그중 최종 근거로 남은 것 — 힌트 덕분에 맞힌 것인지 평가에서 구분한다
        refs = {a.item_id: {f"{e.doc_id}:{e.section_id}" for e in a.evidence} for a in guarded.assessments}
        guarded.meta.debug["hints"] = {
            "enabled": self.use_hints,
            "items": {i: {"given": h, "adopted": [r for r in h if r in refs[i]]} for i, h in hints.items()},
        }
        # 질문 근거는 가드를 통과한 인용만 남긴다
        for q in guarded.questions:
            ok = set().union(*(refs[i] for i in q.item_ids if i in refs))
            q.basis_refs = [r for r in q.basis_refs if r in ok]
        return guarded

    # ---- 항목 하나 ----
    def _assess(self, item: Item, inp: Agent2Input, shared: str, usage: _Usage) -> tuple[Assessment, Optional[Question], list[str]]:
        ctx = inp.context
        allowed = self._baseline_docs(ctx.baseline_id)
        # 계약서·변경합의서는 짧고(조항 10개 안팎) 절차·하자·승인 원칙이 거의 모든 판단에 걸리므로 항상 후보에 둔다
        base: list[tuple[str, str]] = [k for k, s in sections().items() if k[0] in allowed
                                       and s["doc_type"] in ("contract", "change_order")]
        hints: list[str] = []
        if self.use_hints:                               # ①이 붙인 기능 후보는 검색 없이 후보에 넣는다 (개발용 힌트)
            for ref in item.feature_candidates:
                doc_id, _, sec = ref.partition(":")
                if doc_id in allowed and (doc_id, sec) in sections() and (doc_id, sec) not in base:
                    base.append((doc_id, sec))
                    hints.append(ref)

        item_desc = json.dumps({"item_id": item.item_id, "intent": item.intent, "text": item.text,
                                "spans": [s.text for s in item.spans], "inferred": item.inferred}, ensure_ascii=False)
        plan = self._ask(PLAN_SYSTEM, f"{shared}\n\n<item>\n{item_desc}\n</item>", SearchPlan, "submit_search_plan", usage)
        queries, judgment, found = plan.queries or [item.text], None, []
        for _ in range(self.max_search_rounds):
            found = self._search(queries, plan.doc_types, ctx.baseline_id, found, exclude=set(base))
            keys = base + found
            cands = json.dumps([_candidate(k) for k in keys], ensure_ascii=False, indent=1)
            judgment = self._ask(
                JUDGE_SYSTEM, f"{shared}\n\n<item>\n{item_desc}\n</item>\n\n<candidates>\n{cands}\n</candidates>",
                Judgment, "submit_judgment", usage)
            if judgment.sufficient or not judgment.next_queries:
                break
            queries = judgment.next_queries
        return (self._to_assessment(item, judgment, keys, inp.message_text),
                self._to_question(item, judgment, ctx.case_id), hints)

    def _search(self, queries: list[str], doc_types: list[str], baseline_id: str, found: list,
                exclude: set) -> list[tuple[str, str]]:
        """이번 검색 결과를 앞에, 이전 라운드 결과를 뒤에 두고 max_found에서 자른다. 기본 후보(exclude)는 따로 붙는다."""
        try:
            results = [search_docs(q, baseline_id, top_k=5, filters={"doc_types": doc_types} if doc_types else None)
                       for q in queries[:3]]
        except ToolInputError:
            raise
        except Exception as e:
            raise AgentFailure(AgentError(code="RETRIEVAL_UNAVAILABLE", message=type(e).__name__, stage="agent2", retryable=True))
        merged = [k for k in _rrf(results) if k not in exclude]
        merged += [k for k in found if k not in merged]
        return merged[:self.max_found]                   # 판단 프롬프트가 너무 길어지지 않도록

    @staticmethod
    def _baseline_docs(baseline_id: str) -> set[str]:
        bl = baselines().get(baseline_id)
        if bl is None:
            raise ToolInputError(f"알 수 없는 baseline_id: {baseline_id}")
        return set(bl["document_ids"])

    @staticmethod
    def _to_assessment(item: Item, j: Judgment, keys: list, message_text: str = "") -> Assessment:
        evidence = []
        for c in j.evidence:
            if (c.doc_id, c.section_id) not in keys:      # 후보 밖 문서는 쓰지 않는다
                continue
            s = get_section(c.doc_id, c.section_id)
            evidence.append(Evidence(doc_id=c.doc_id, doc_version=s["doc_version"], section_id=c.section_id,
                                     page=s["page"], line_start=s["line_start"], line_end=s["line_end"],
                                     locator=s["locator"], extraction_version=s["extraction_version"],
                                     quote=c.quote, role=c.role, match_type=c.match_type))
        label, missing, level = j.label, list(j.missing_info), j.evidence_level
        if label != "unclear" and not evidence:          # 후보 밖 인용만 있었다 — 스키마 검증 전에 보류로 내린다
            label, level = "unclear", "none"
            missing.append("후보 문서 안의 근거 없음 — 판단 보류")
        elif label == "approved_change" and not j.approved_change_ref:
            label, level = "unclear", "none"
            missing.append("승인한 변경합의서 참조 없음 — 판단 보류")
        elif label == "in_scope" and item.intent == "issue_report" and not _facts_in(j.defect_facts, message_text):
            # 원인 불명 제보를 결함으로 단정하려면 원문의 사실(재현 조건·로그·대상 건)을 인용해야 한다 — 코드가 원문과 대조
            label, level = "unclear", "none"
            missing.append("결함으로 볼 사실(재현 조건·로그·대상 건)이 원문에 없음 — 판단 보류")
        if label == "unclear" and not missing:
            missing = [j.question.reason if j.question else "판단할 근거·정보 부족"]
        ref = j.approved_change_ref if label == "approved_change" else None
        if ref and ref not in documents():               # 'CO-A-001' 처럼 버전 없이 쓴 경우
            ref = next((d for d in documents() if d.startswith(ref)), ref)
        return Assessment(item_id=item.item_id, label=label, evidence=evidence, rationale=j.rationale,
                          evidence_level=level, missing_info=missing, uncertainties=j.uncertainties,
                          approved_change_ref=ref)

    @staticmethod
    def _to_question(item: Item, j: Judgment, case_id: str) -> Optional[Question]:
        q = j.question
        if q is None:
            return None
        return Question(question_id="", case_id=case_id, stage="agent2", item_ids=[item.item_id], field=q.field,
                        text=q.text, reason=q.reason, basis_refs=[f"{e.doc_id}:{e.section_id}" for e in j.evidence],
                        target=q.target, blocking=q.blocking)

    @staticmethod
    def _shared_context(inp: Agent2Input, baseline: dict) -> str:
        docs = [{"doc_id": d["doc_id"], "doc_type": d["doc_type"], "priority_rank": d["priority_rank"]} for d in baseline["documents"]]
        return "\n".join([
            f"<baseline id=\"{inp.context.baseline_id}\" as_of=\"{inp.context.baseline_as_of}\">",
            json.dumps(docs, ensure_ascii=False), "</baseline>",
            "<request_message>", inp.message_text, "</request_message>",
            "<constraints>", json.dumps([c.text for c in inp.constraints], ensure_ascii=False), "</constraints>",
            "<answers>", json.dumps([{"question_id": a.question_id, "answered_by": a.answered_by, "text": a.text}
                                     for a in inp.answers], ensure_ascii=False), "</answers>",
        ])
