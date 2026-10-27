"""ID와 시각은 호출부가 발급한다. LLM은 실행 ID를 만들지 않는다 (공통기준 1장)."""
import uuid
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))


def new_result_id(stage: str, case_id: str) -> str:
    return f"{stage}-{case_id}-{uuid.uuid4().hex[:8]}"


def now_iso() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")
