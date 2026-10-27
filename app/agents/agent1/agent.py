"""① 요청 구조화 에이전트 (담당: 고수연).

흐름: 전처리(인사·서명 제거, 인젝션 문구 표시) → 항목 추출 LLM(구조화 출력) → 검증(스키마·span·ID)
      → 기능 후보 붙이기(search_docs 1회) → 모호함 점검 LLM → items + questions
평가: 항목 F1, intent 정확도, 필수/조건 보존, span 정확 일치, 질문 필요 사례 재현율, rerun_from 정확도
"""
from schema.schemas import Agent1Input, Agent1Output, AnswerAnalysisInput, AnswerAnalysisOutput

from app.agents.agent1.prompts import ANSWER_SYSTEM, EXTRACT_SYSTEM, QUESTION_SYSTEM  # noqa: F401


def check_spans(inp: Agent1Input, out: Agent1Output) -> list[str]:
    """span 검사 (코드). message_text[start:end] == text 가 아닌 항목 목록을 돌려준다."""
    bad = []
    for it in out.items:
        for sp in it.spans:
            if inp.message_text[sp.start:sp.end] != sp.text:
                bad.append(f"{it.item_id}: span {sp.start}:{sp.end}")
    return bad


class LlmAgent1:
    def run(self, inp: Agent1Input) -> Agent1Output:
        raise NotImplementedError("① LLM 구현 예정")

    def analyze_answer(self, inp: AnswerAnalysisInput) -> AnswerAnalysisOutput:
        raise NotImplementedError("① 답변 분석 구현 예정")
