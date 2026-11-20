"""공식 금액·일정 계산의 얇은 어댑터. 계산 공식은 Backend에만 둔다."""
from app.tools.backend_calc import calculation_client

FORMULA_VERSION = "calc-v1"


def calc_pricing(lines: list[dict], rate_card_id: str, *, project_id: str | None = None,
                 adjustments: dict | None = None, previous: dict | None = None, as_of: str | None = None) -> dict:
    result = calculation_client(rate_card_id).calculate({
        "rate_card_id": rate_card_id, "project_id": project_id, "lines": lines,
        "adjustments": adjustments, "previous": previous, "as_of": as_of,
    })
    if result.get("pricing"):
        result["pricing"].update(result.get("snapshot") or {})
    return result
