"""① 요청 구조화 에이전트: LLM 초안을 결정적인 공통 출력으로 조립한다."""

from dataclasses import asdict

from schema.schemas import (
    Agent1Input,
    Agent1Output,
    AnswerAnalysisInput,
    AnswerAnalysisOutput,
    Meta,
)

from app.agents.agent1.assemble import FeatureSearch, assemble, default_feature_search
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
        feature_search: FeatureSearch = default_feature_search,
    ) -> None:
        """Store the structured LLM client and feature-candidate search function."""
        self.llm = llm
        self.feature_search = feature_search

    def run(self, inp: Agent1Input) -> Agent1Output:
        """Create a validated Agent1 result with application-owned IDs and spans."""
        result = self.llm.complete_structured(
            system=SYSTEM,
            user=build_user_prompt(inp),
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
        raise NotImplementedError("① 답변 분석 구현 예정")
