"""에이전트 인터페이스. 에이전트끼리는 공통 스키마(schema/schemas.py)로만 연결된다.

내부 구현(모델·프레임워크·검색)은 담당자 자유. 출력의 뜻·필드를 바꾸려면 공통기준 13장 절차를 따른다.
"""
from dataclasses import dataclass
from typing import Protocol

from schema.schemas import (
    AgentError, Agent1Input, Agent1Output, Agent2Input, Agent2Output, Agent3Input, Agent3Output,
    AnswerAnalysisInput, AnswerAnalysisOutput,
)


class AgentFailure(Exception):
    """기술 실패만. 판단 보류·근거 없음·산정 불가는 예외가 아니라 정상 출력(unclear, not_estimable)."""

    def __init__(self, error: AgentError):
        super().__init__(error.message)
        self.error = error


class Agent1(Protocol):
    def run(self, inp: Agent1Input) -> Agent1Output: ...
    def analyze_answer(self, inp: AnswerAnalysisInput) -> AnswerAnalysisOutput: ...


class Agent2(Protocol):
    def run(self, inp: Agent2Input) -> Agent2Output: ...


class Agent3(Protocol):
    def run(self, inp: Agent3Input) -> Agent3Output: ...


@dataclass
class AgentSet:
    agent1: Agent1
    agent2: Agent2
    agent3: Agent3
