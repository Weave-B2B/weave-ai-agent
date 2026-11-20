"""기존 Agent3 인건비 도구 계약을 Backend 공식 계산으로 연결한다."""
import math
from decimal import Decimal

from app.tools.backend_calc import calculation_client
from app.tools.errors import ToolInputError
from app.tools.trace import traced


@traced("calc_cost")
def calc_cost(lines: list[dict], rate_card_id: str, *, project_id: str | None = None) -> dict:
    if not isinstance(lines, list) or any(not isinstance(line, dict) for line in lines):
        raise ToolInputError("lines must be a list of objects")
    # 전송할 수 없는 타입/NaN은 HTTP 호출 전에 거절한다. 금액·공수 계산은 Backend가 담당한다.
    for line in lines:
        md = line.get("md")
        if isinstance(md, bool) or not isinstance(md, (int, float, Decimal)) or not math.isfinite(md):
            raise ToolInputError("md must be a finite JSON number")
    return calculation_client(rate_card_id).labor_totals({
        "rate_card_id": rate_card_id, "project_id": project_id,
        "lines": [{"key": f"L{n + 1}", "role": line.get("role"), "md": line.get("md")}
                  for n, line in enumerate(lines)],
    })
