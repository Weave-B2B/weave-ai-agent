"""금액 계산 — LLM이 쓰지 않는다. (운영에서는 백엔드 API로 교체)

반환: 성공 = Totals 형태 dict / 단가 누락 = {"status":"NEEDS_INPUT","missing":[...],"totals":None}
입력 오류(없는 단가표·직군, 음수·0.5단위 아님 MD) = ToolInputError(INVALID_INPUT)
"""
from decimal import Decimal, ROUND_HALF_UP

from app.kb.loader import rate_cards
from app.tools.errors import ToolInputError
from app.tools.trace import traced

ROLES = ("PM", "UX", "FE", "BE", "QA")


def _won(x: Decimal) -> int:
    return int(x.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


@traced("calc_cost")
def calc_cost(lines: list[dict], rate_card_id: str) -> dict:
    cards = rate_cards()
    if rate_card_id not in cards:
        raise ToolInputError(f"unknown rate_card_id {rate_card_id}")
    rates = cards[rate_card_id]["rates"]

    by: dict[str, Decimal] = {}
    for ln in lines:
        role, md = ln["role"], Decimal(str(ln["md"]))
        if role not in ROLES:
            raise ToolInputError(f"unknown role {role}")
        if not md.is_finite() or md < 0 or (md * 2) % 1:
            raise ToolInputError(f"md must be >=0 in 0.5 steps: {md}")
        by[role] = by.get(role, Decimal(0)) + md

    # 0MD 직군은 단가가 없어도 무방. 단가가 null이어도 누락으로 본다.
    missing = sorted(f"rate:{r}" for r, m in by.items()
                     if m > 0 and (r not in rates or rates[r].get("cost") is None or rates[r].get("price") is None))
    if missing:
        return {"status": "NEEDS_INPUT", "missing": missing, "totals": None}

    pos = {r: m for r, m in by.items() if m > 0}
    cost = _won(sum((m * Decimal(rates[r]["cost"]) for r, m in pos.items()), Decimal(0)))
    price = _won(sum((m * Decimal(rates[r]["price"]) for r, m in pos.items()), Decimal(0)))
    margin = None if price == 0 else float(
        (Decimal(price - cost) / Decimal(price)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))
    return {"md": float(sum(by.values(), Decimal(0))),
            "md_by_role": {r: float(by[r]) for r in ROLES if r in by},
            "cost": cost, "price": price, "margin": margin,
            "currency": "KRW", "vat_included": False, "rate_card_id": rate_card_id}
