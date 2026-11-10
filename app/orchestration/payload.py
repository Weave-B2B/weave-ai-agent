"""Backend에 커밋하는 결과 payload의 운영 계약 정리.

평가 전용 필드(gold_*)와 자유형 meta.debug는 Backend 저장 결과에 싣지 않는다.
실행 telemetry는 payload가 아니라 meta의 model/latency_ms/cost_usd/trace_id로만 남긴다.
context는 입력과 같아야 하므로(preflight 검사) 건드리지 않는다. scenario_id는 운영 입력에서 null이어야 한다(Context 스키마 설명).
meta.result_revision은 Backend가 부여한 값이 아니므로 권위 있는 revision은 CommittedResult.result_revision이다.
"""
from typing import Any

EVAL_ONLY_KEYS = frozenset({"gold_origin", "gold_ref", "channel_src"})


def _strip(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _strip(v) for k, v in obj.items() if k not in EVAL_ONLY_KEYS}
    if isinstance(obj, list):
        return [_strip(v) for v in obj]
    return obj


def to_commit_payload(output) -> dict:
    data = _strip(output.model_dump(mode="json"))
    data["meta"]["debug"] = {}
    return data
