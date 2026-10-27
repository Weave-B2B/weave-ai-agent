"""도구 호출 로그: result_id·도구·입출력 해시·시각·성공 여부 (EVALUATION.md '도구 출처').

'totals_source=calc_cost' 문자열만으로는 실제 호출을 증명할 수 없으므로 호출을 기록한다.
프롬프트 전문·개인정보는 남기지 않고 해시만 남긴다.
"""
import functools
import hashlib
import json
from contextvars import ContextVar
from datetime import datetime, timezone

TOOL_LOG: list[dict] = []
current_result_id: ContextVar[str | None] = ContextVar("current_result_id", default=None)


def _h(obj) -> str:
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()[:16]


def traced(name: str, version: str = "1"):
    def deco(fn):
        @functools.wraps(fn)
        def wrap(*a, **kw):
            rec = {"result_id": current_result_id.get(), "tool": name, "version": version,
                   "in": _h([a, kw]), "at": datetime.now(timezone.utc).isoformat(), "ok": False}
            try:
                out = fn(*a, **kw)
                rec["out"], rec["ok"] = _h(out), True
                return out
            finally:
                TOOL_LOG.append(rec)
        return wrap
    return deco
