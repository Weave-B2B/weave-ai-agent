"""③ 영향 산정 + 초안 에이전트 (담당: 임기민).

흐름: 작업 분해 계획 LLM → 산정 가능? (아니오 → not_estimable) → 직군·MD 산정 LLM(0.5 단위, 가정 명시)
      → get_schedule → 일정 영향 LLM → calc_cost(코드) → 초안 작성 LLM(템플릿 복사)
산정 대상은 PM의 estimate=true (Agent3Input.targets()). ②의 label이 아니라 pm_label을 따른다.
평가: 작업 누락·중복, not_estimable 정확도, calc_cost 재현, 금액 LLM 작성 0건. MD 정확도는 검수 전 비활성.
"""
from schema.schemas import Agent3Input, Agent3Output

from app.agents.agent3.prompts import DRAFT_TEMPLATE, ESTIMATE_SYSTEM, PLAN_SYSTEM  # noqa: F401


class LlmAgent3:
    def run(self, inp: Agent3Input) -> Agent3Output:
        raise NotImplementedError("③ LLM 구현 예정")
