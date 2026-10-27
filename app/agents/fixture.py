"""Fixture 에이전트: 데이터셋의 정답(chain)을 에이전트 출력 대신 돌려준다.

오케스트레이션·API·계약 테스트를 LLM 없이 돌리기 위한 것이다 (공통기준 13장 '가짜 출력').
출력의 context는 입력 context를 따른다 (원문 revision이 바뀐 재요청도 흐름만 검증).
정답을 에이전트 성능 측정에 쓰거나 검색 색인에 넣지 않는다.
"""
import json

from schema.schemas import (
    Agent1Input, Agent1Output, Agent2Input, Agent2Output, Agent3Input, Agent3Output,
    AnswerAnalysisInput, AnswerAnalysisOutput,
)

from app.agents.base import AgentSet
from app.core.config import settings


def load_chain() -> dict[str, dict]:
    rows: dict[str, dict] = {}
    for split in ("train", "validation", "test"):
        p = settings.eval_dir / "chain" / f"{split}.jsonl"
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                rows[r["case_id"]] = r
    return rows


class _Base:
    def __init__(self, chain: dict[str, dict]):
        self.chain = chain


class FixtureAgent1(_Base):
    def run(self, inp: Agent1Input) -> Agent1Output:
        return Agent1Output.model_validate(self.chain[inp.context.case_id]["agent1_gold"]).model_copy(update={"context": inp.context})

    def analyze_answer(self, inp: AnswerAnalysisInput) -> AnswerAnalysisOutput:
        return AnswerAnalysisOutput(context=inp.context, question_id=inp.question.question_id, resolved=True,
                                    rerun_from=None, summary="fixture: 영향 없음")


class FixtureAgent2(_Base):
    def run(self, inp: Agent2Input) -> Agent2Output:
        return Agent2Output.model_validate(self.chain[inp.context.case_id]["agent2_gold"]).model_copy(update={"context": inp.context})


class FixtureAgent3(_Base):
    def run(self, inp: Agent3Input) -> Agent3Output:
        return Agent3Output.model_validate(self.chain[inp.context.case_id]["agent3_gold"]).model_copy(update={"context": inp.context})


def fixture_agents() -> AgentSet:
    chain = load_chain()
    return AgentSet(FixtureAgent1(chain), FixtureAgent2(chain), FixtureAgent3(chain))
