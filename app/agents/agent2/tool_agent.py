"""② 도구 사용 에이전트: LLM이 매 턴 다음 행동(도구 호출 또는 최종 판단)을 고른다.

    관찰(항목·기준선·지금까지의 도구 결과) → LLM이 행동 선택 → 코드가 도구 실행 → 결과를 관찰에 추가 → 반복
    LLM이 submit_judgment를 고르면 끝난다. max_steps를 넘기면 도구 없이 최종 판단만 요청한다.

LLM이 고르는 것: 어떤 도구를, 어떤 인자로, 몇 번, 언제 끝낼지.
코드가 강제하는 것 (LLM이 건너뛸 수 없음):
  - 도구는 이 실행의 기준선 문서만 보여 준다 (baseline_id는 LLM 인자가 아니다)
  - 근거로 쓸 수 있는 것은 이 실행에서 실제로 본 섹션뿐이다
  - 최종 판단은 기존과 같은 가드(인용 검증·근거 없음 → unclear)를 거친다
공통 클라이언트는 한 번에 구조화 출력 하나만 지원하므로, 행동 선택을 구조화 출력(AgentStep)으로 받는다.
"""
import json
from typing import Literal, Optional

from pydantic import Field
from schema.schemas import AgentError, Agent2Input, Assessment, Item, Question

from app.agents.agent2.agent import DocType, Judgment, LlmAgent2, _LlmOut, _Usage
from app.agents.agent2.prompts import AGENT_SYSTEM, FINAL_SYSTEM
from app.agents.base import AgentFailure
from app.kb.loader import sections
from app.tools.doc_tools import get_section, search_docs, verify_quote
from app.tools.errors import ToolInputError

AGENT_VERSION = "a2-agent-0.1.0"
Action = Literal["search_docs", "get_section", "verify_quote", "submit_judgment"]


class AgentStep(_LlmOut):
    """LLM이 고르는 다음 행동 하나. action에 맞는 인자만 채운다."""
    thought: str = Field(description="이 행동을 고른 이유 한두 문장")
    action: Action
    query: Optional[str] = Field(None, description="search_docs: 검색어")
    doc_types: list[DocType] = Field([], description="search_docs: 좁힐 문서 종류. 비우면 전체")
    doc_id: Optional[str] = Field(None, description="get_section·verify_quote: 문서 ID")
    section_id: Optional[str] = Field(None, description="get_section·verify_quote: 섹션 ID")
    quote: Optional[str] = Field(None, description="verify_quote: 확인할 인용문")
    judgment: Optional[Judgment] = Field(None, description="submit_judgment: 최종 판단")


class ToolAgent2(LlmAgent2):
    agent_version = AGENT_VERSION
    max_steps = 8                                        # 항목당 도구 호출 상한 (비용·지연 울타리)
    snippet_chars = 160                                  # 검색 결과는 앞부분만 보여 주고 전문은 get_section으로

    def _assess(self, item: Item, inp: Agent2Input, shared: str, usage: _Usage) -> tuple[Assessment, Optional[Question], list[str]]:
        ctx = inp.context
        allowed = self._baseline_docs(ctx.baseline_id)
        hints = [r for r in item.feature_candidates if r.partition(":")[0] in allowed] if self.use_hints else []
        item_desc = json.dumps({"item_id": item.item_id, "intent": item.intent, "text": item.text,
                                "spans": [s.text for s in item.spans], "inferred": item.inferred,
                                "feature_hints": hints}, ensure_ascii=False)
        seen: list[tuple[str, str]] = []                 # 이번 실행에서 실제로 본 섹션 = 근거로 쓸 수 있는 범위
        read: dict[tuple[str, str], dict] = {}           # get_section으로 읽은 전문 — 관찰에 한 번만 싣는다
        history: list[dict] = []
        judgment: Optional[Judgment] = None
        index = self._contract_index(allowed)

        def observe(steps_left: int) -> str:
            return self._observation(shared, item_desc, index, read, history, steps_left)

        pushed_back = False
        for n in range(self.max_steps):
            step = self._ask(AGENT_SYSTEM, observe(self.max_steps - n), AgentStep, "choose_next_action", usage)
            if step.action == "submit_judgment" and step.judgment is not None:
                gaps = [] if pushed_back or n == self.max_steps - 1 else self._precheck(item, hints, seen, read, allowed)
                if gaps:                                 # 제출 전 점검 — 한 번만 되돌려 빠진 조사를 하게 한다
                    pushed_back = True
                    history.append({"action": "submit_judgment", "result": {"rejected": "제출 전 확인 필요", "gaps": gaps}})
                    continue
                judgment = step.judgment
                history.append({"action": "submit_judgment"})
                break
            obs = self._run_tool(step, ctx.baseline_id, allowed, seen, read)
            history.append({"thought": step.thought, "action": step.action,
                            "args": step.model_dump(include={"query", "doc_types", "doc_id", "section_id", "quote"},
                                                    exclude_none=True),
                            "result": obs})
        if judgment is None:                             # 상한 도달 — 본 것만으로 판단을 강제한다
            judgment = self._ask(FINAL_SYSTEM, observe(0), Judgment, "submit_judgment", usage)

        usage.trace(item.item_id, [h["action"] for h in history], [self._step_brief(h) for h in history])
        return (self._to_assessment(item, judgment, seen, inp.message_text),
                self._to_question(item, judgment, ctx.case_id), hints)

    # ---- 도구 실행 (울타리는 여기) ----
    def _run_tool(self, step: AgentStep, baseline_id: str, allowed: set[str], seen: list, read: dict) -> dict:
        try:
            if step.action == "search_docs":
                if not step.query:
                    return {"error": "query 필요"}
                try:
                    res = search_docs(step.query, baseline_id, top_k=5,
                                      filters={"doc_types": step.doc_types} if step.doc_types else None)
                except ToolInputError:
                    raise
                except Exception as e:
                    raise AgentFailure(AgentError(code="RETRIEVAL_UNAVAILABLE", message=type(e).__name__,
                                                  stage="agent2", retryable=True))
                for r in res:
                    self._see(seen, (r["doc_id"], r["section_id"]))
                return {"results": [{"doc_id": r["doc_id"], "section_id": r["section_id"],
                                     "heading": sections()[(r["doc_id"], r["section_id"])]["heading"],
                                     # 이미 읽은 섹션은 snippet을 다시 싣지 않는다
                                     **({"read": True} if (r["doc_id"], r["section_id"]) in read
                                        else {"snippet": r["text"][:self.snippet_chars]})} for r in res]}
            if not step.doc_id or not step.section_id:
                return {"error": "doc_id·section_id 필요"}
            if step.doc_id not in allowed:               # 다른 프로젝트·기준선 밖 문서는 열어 주지 않는다
                return {"error": f"{step.doc_id}는 이 기준선 문서가 아님"}
            if step.action == "get_section":
                key = (step.doc_id, step.section_id)
                if key in read:
                    return {"note": "이미 읽음 — <read_sections> 참조"}
                s = get_section(*key)
                self._see(seen, key)
                meta = sections()[key]
                read[key] = {"doc_id": step.doc_id, "section_id": step.section_id, "doc_type": meta["doc_type"],
                             "priority_rank": meta["priority_rank"], "heading": meta["heading"], "text": s["text"]}
                return {"read": f"{step.doc_id}:{step.section_id}", "heading": meta["heading"]}   # 전문은 <read_sections>
            if step.action == "verify_quote":
                return verify_quote(step.quote or "", step.doc_id, step.section_id)
            return {"error": "judgment 없이 submit_judgment"}
        except ToolInputError as e:                      # 잘못된 섹션 ID 등 — 에이전트가 고칠 수 있게 결과로 돌려준다
            return {"error": str(e)}

    @staticmethod
    def _see(seen: list, key: tuple[str, str]) -> None:
        if key not in seen:
            seen.append(key)

    @staticmethod
    def _precheck(item: Item, hints: list[str], seen: list, read: dict, allowed: set[str]) -> list[str]:
        """프롬프트로는 지켜지지 않던 조사 누락을 코드가 잡는다 (평가 트레이스에서 반복 확인된 두 가지)."""
        gaps = []
        unseen = [h for h in hints if tuple(h.split(":", 1)) not in seen]
        if unseen:
            gaps.append(f"①이 준 기능 후보 {unseen}를 아직 보지 않았다. get_section으로 읽고 판단에 반영하라")
        if item.intent == "defect":
            defect = [k for k, s in sections().items() if k[0] in allowed and s["doc_type"] == "contract"
                      and "하자" in s["heading"]]
            if defect and not any(k in read for k in defect):
                gaps.append(f"결함(defect) 항목인데 계약서 하자 조항 {[f'{d}:{c}' for d, c in defect]}을 읽지 않았다")
        return gaps

    @staticmethod
    def _step_brief(h: dict) -> dict:
        """행동 하나를 짧게: 검색어와 결과 순위(via 포함), 읽은 조항, 오류. 원문은 넣지 않는다."""
        out = {"action": h["action"], **h.get("args", {})}
        r = h.get("result") or {}
        if "results" in r:
            out["hits"] = [f"{x['doc_id']}:{x['section_id']}" + ("*" if x.get("read") else "") for x in r["results"]]
        for k in ("read", "note", "error", "rejected", "gaps"):
            if k in r:
                out[k] = r[k]
        return out

    @staticmethod
    def _contract_index(allowed: set[str]) -> list[dict]:
        """계약서·변경합의서 조항 목록(제목만). 하자·우선순위·변경 승인 조항을 놓치지 않게 첫 관찰부터 보여 준다.
        목록에 있다고 근거로 쓸 수 있는 것은 아니다 — 인용하려면 get_section으로 읽어야 한다."""
        return [{"doc_id": s["doc_id"], "section_id": s["section_id"], "doc_type": s["doc_type"], "heading": s["heading"]}
                for (d, _), s in sections().items() if d in allowed and s["doc_type"] in ("contract", "change_order")]

    @staticmethod
    def _observation(shared: str, item_desc: str, index: list[dict], read: dict, history: list[dict],
                     steps_left: int) -> str:
        dump = lambda x: json.dumps(x, ensure_ascii=False, separators=(",", ":"))
        return "\n".join([shared, "", "<item>", item_desc, "</item>", "",
                          "<contract_sections>", dump(index), "</contract_sections>", "",
                          "<read_sections>", dump(list(read.values())), "</read_sections>", "",
                          "<history>", dump(history), "</history>",
                          f"<steps_left>{steps_left}</steps_left>"])
