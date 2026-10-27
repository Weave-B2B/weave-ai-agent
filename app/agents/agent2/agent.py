"""② 근거 검색·범위 판단 에이전트 (담당: 선승규).

흐름: 검색 계획 LLM → search_docs(기준선 필터) → 근거 충분? (부족하면 재검색, 최대 3회)
      → get_section → 판단 LLM → verify_quote(코드) → 가드(apply_guards) → assessment
평가: 라벨 정확도·macro-F1, unclear 정밀도/재현율, Hit@5·MRR, 인용 존재율·지지율, 근거 없는 out_of_scope 0건
"""
from schema.schemas import Agent2Input, Agent2Output

from app.agents.agent2.prompts import JUDGE_SYSTEM, PLAN_SYSTEM  # noqa: F401


class LlmAgent2:
    max_search_rounds = 3

    def run(self, inp: Agent2Input) -> Agent2Output:
        raise NotImplementedError("② LLM 구현 예정")
