"""케이스 상태 저장. MVP는 메모리, 운영은 Postgres 체크포인터(thread_id = case_id)로 교체.
재시작 후에도 WAITING_* 상태를 복원해야 한다 (공통기준 11-2)."""
import threading
from dataclasses import dataclass, field
from typing import Optional, Protocol

from schema.schemas import (
    Agent1Input, Agent1Output, Agent2Output, Agent3Output, Answer, Item, PMReview, Question, WorkflowState,
)


@dataclass
class CaseRecord:
    inp: Agent1Input
    rate_card_id: str
    state: WorkflowState
    a1: Optional[Agent1Output] = None
    items: Optional[list[Item]] = None        # PM이 확인한 항목 (A2 입력의 기준)
    a2: Optional[Agent2Output] = None
    pm_review: Optional[PMReview] = None
    a3: Optional[Agent3Output] = None
    answers: list[Answer] = field(default_factory=list)
    questions: dict[str, Question] = field(default_factory=dict)
    archive: dict[str, dict] = field(default_factory=dict)   # 무효화(STALE)된 결과는 삭제하지 않고 보존

    def view(self) -> dict:
        d = lambda m: m.model_dump(mode="json") if m is not None else None
        return {"state": d(self.state), "a1": d(self.a1), "items": [d(i) for i in self.items] if self.items else None,
                "a2": d(self.a2), "pm_review": d(self.pm_review), "a3": d(self.a3),
                "questions": [d(q) for q in self.questions.values()], "answers": [d(a) for a in self.answers]}


class StateStore(Protocol):
    def get(self, case_id: str) -> Optional[CaseRecord]: ...
    def put(self, rec: CaseRecord) -> None: ...


class InMemoryStore:
    def __init__(self):
        self._d: dict[str, CaseRecord] = {}
        self.lock = threading.RLock()

    def get(self, case_id):
        return self._d.get(case_id)

    def put(self, rec):
        self._d[rec.inp.context.case_id] = rec
