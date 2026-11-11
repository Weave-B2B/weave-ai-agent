"""① 요청 구조화 에이전트: LLM 초안을 결정적인 공통 출력으로 조립한다."""

from dataclasses import asdict

from schema.schemas import (
    Agent1Input,
    Agent1Output,
    AnswerAnalysisInput,
    AnswerAnalysisOutput,
    Meta,
)

from app.agents.agent1.assemble import FeatureSearch, assemble
from app.agents.agent1.draft import Agent1Draft
from app.agents.agent1.prompts import PROMPT_VERSION, SYSTEM, build_user_prompt
from app.llm.client import LlmClient


def check_spans(inp: Agent1Input, out: Agent1Output) -> list[str]:
    """span 검사 (코드). message_text[start:end] == text 가 아닌 항목 목록을 돌려준다."""
    bad = []
    for it in out.items:
        for sp in it.spans:
            if inp.message_text[sp.start:sp.end] != sp.text:
                bad.append(f"{it.item_id}: span {sp.start}:{sp.end}")
    return bad


class LlmAgent1:
    """Generate an Agent1 draft and deterministically assemble its final output."""

    def __init__(
        self,
        llm: LlmClient,
        *,
        feature_search: FeatureSearch | None = None,
        system_suffix: str = "",
    ) -> None:
        """Store the structured LLM client and feature-candidate search function."""
        self.llm = llm
        self.feature_search = feature_search
        self.system_suffix = system_suffix

    def run(self, inp: Agent1Input) -> Agent1Output:
        """Create a validated Agent1 result with application-owned IDs and spans."""
        result = self.llm.complete_structured(
            system=SYSTEM + self.system_suffix,
            user=build_user_prompt(inp, prompt_version=PROMPT_VERSION),
            schema=Agent1Draft,
            tool_name="submit_items",
        )
        output, report = assemble(
            inp,
            result.parsed,
            feature_search=self.feature_search,
        )
        meta = Meta(
            model=getattr(result, "label", f"{result.provider}:{result.model_id}"),
            latency_ms=result.latency_ms,
            agent_version="agent1-0.2.0",
            cost_usd=None,
            debug={
                "prompt_version": PROMPT_VERSION,
                "retried": result.retried,
                "input_tokens": result.usage.input_tokens,
                "output_tokens": result.usage.output_tokens,
                **asdict(report),
            },
        )
        return output.model_copy(update={"meta": meta})

    def analyze_answer(self, inp: AnswerAnalysisInput) -> AnswerAnalysisOutput:
        """Deterministically interpret one PM/client answer without creating a false resolution."""
        text = inp.answer.text.strip()
        affected = list(inp.question.item_ids)
        changed_composition = any(
            marker in text
            for marker in ("추가", "제외", "수정", "수량", "대상", "기능", "항목", "확인")
        )
        resolved = bool(text) and not any(
            marker in text.lower()
            for marker in ("모르겠", "확인 후", "확인 필요", "미정")
        )

        rerun_from = None
        if resolved and changed_composition:
            rerun_from = "agent1"
        elif resolved and inp.question.field in {"scope", "contract_scope", "baseline", "document"}:
            rerun_from = "agent2"

        return AnswerAnalysisOutput(
            context=inp.context,
            question_id=inp.question.question_id,
            resolved=resolved,
            rerun_from=rerun_from,
            affected_item_ids=affected,
            summary="답변 내용에 항목 구성 변경이 있으면 Agent 1 재분리, 범위 근거 변경이면 Agent 2 재판단 대상으로 사용합니다."
            if changed_composition or resolved
            else "답변이 불완전하여 질문을 해소하지 않습니다.",
        )
