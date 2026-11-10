"""User-visible activity for one local prototype case."""
from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
from threading import Lock

from app.tools.trace import tool_activity


_current = ContextVar("supervisor_progress", default=None)
_parent = ContextVar("supervisor_progress_parent", default=None)
_TOOLS = {
    "list_baseline_docs": ("문서 목록 확인", "기준선의 문서 목록을 확인하고 있습니다"),
    "search_docs": ("계약 문서 검색", "계약 문서에서 관련 조항을 검색하고 있습니다"),
    "get_section": ("조항 원문 확인", "관련 조항의 원문을 확인하고 있습니다"),
    "verify_quote": ("인용 검증", "인용과 원문이 일치하는지 확인하고 있습니다"),
    "get_schedule": ("계약 일정 조회", "계약 일정과 변경 이력을 조회하고 있습니다"),
    "calc_cost": ("금액 계산", "공수와 단가를 적용해 금액을 계산하고 있습니다"),
}


class CaseProgress:
    def __init__(self, events=()):
        self._lock = Lock()
        self._events = deque(events, maxlen=200)
        self._seq = self._events[-1]["seq"] if self._events else 0

    def _record(self, **fields):
        with self._lock:
            self._seq += 1
            self._events.append({"seq": self._seq, **fields})
            return self._seq

    def start(self, agent, title):
        _parent.set(self._record(kind="step", agent=agent, title=title))

    def after(self, seq=0):
        with self._lock:
            return [event for event in self._events if event["seq"] > seq]

    def _tool_event(self, name, phase, result=None):
        parent = _parent.get()
        if parent is None or name not in _TOOLS:
            return
        label, title = _TOOLS[name]
        detail = {"kind": "tool", "parent_seq": parent, "tool": name, "phase": phase}
        if phase == "start":
            self._record(**detail, label=label, title=title)
        elif phase == "done":
            self._record(**detail, matches=len(result) if name == "search_docs" else None)
        elif phase == "error":
            self._record(**detail)

    @contextmanager
    def capture(self):
        progress_token = _current.set(self)
        parent_token = _parent.set(None)
        tool_token = tool_activity.set(self._tool_event)
        try:
            yield
        finally:
            tool_activity.reset(tool_token)
            _parent.reset(parent_token)
            _current.reset(progress_token)


def record_activity(agent, title):
    progress = _current.get()
    if progress is not None:
        progress.start(agent, title)
