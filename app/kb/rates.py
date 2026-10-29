"""Validate the structured rate authority without importing application settings."""

import json
import math
from pathlib import Path

from app.tools.errors import ToolInputError

ROLES = {"PM", "UX", "FE", "BE", "QA"}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ToolInputError("rate card JSON contains duplicate fields")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ToolInputError("rate card JSON cannot contain NaN or Infinity")


def load_rate_cards(path: Path) -> dict[str, dict]:
    """Load every card freshly; missing/null role prices remain valid unknowns."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object,
            parse_constant=_invalid_constant)
    except (json.JSONDecodeError, UnicodeError):
        raise ToolInputError("rate card file must contain valid JSON") from None
    if not isinstance(data, dict) or not isinstance(data.get("rate_cards"), dict) or not data["rate_cards"]:
        raise ToolInputError("rate_cards must be a nonempty object")
    for card_id, card in data["rate_cards"].items():
        if not isinstance(card_id, str) or not card_id.strip() or not isinstance(card, dict):
            raise ToolInputError("rate card IDs and cards must be valid objects")
        if card.get("unit") != "KRW/MD" or card.get("vat_included") is not False:
            raise ToolInputError("rate cards require KRW/MD and vat_included=false")
        if "currency" in card and card["currency"] != "KRW":
            raise ToolInputError("rate card currency must be KRW")
        if "synthetic" in card and not isinstance(card["synthetic"], bool):
            raise ToolInputError("rate card synthetic flag must be a JSON boolean")
        rates = card.get("rates")
        if not isinstance(rates, dict):
            raise ToolInputError("rates must be an object")
        for role, rate in rates.items():
            if role not in ROLES or not isinstance(rate, dict):
                raise ToolInputError("rates require known roles and cost/price objects")
            if (("unit" in rate and rate["unit"] != "KRW/MD")
                    or ("currency" in rate and rate["currency"] != "KRW")
                    or ("vat_included" in rate and rate["vat_included"] is not False)):
                raise ToolInputError("role rates must agree with KRW/MD and VAT excluded")
            for field in ("cost", "price"):
                value = rate.get(field)
                if value is None:
                    continue
                if (isinstance(value, bool) or not isinstance(value, (int, float))
                        or value < 0 or (isinstance(value, float) and not math.isfinite(value))):
                    raise ToolInputError("rate cost/price must be finite nonnegative JSON numbers or null")
    return data["rate_cards"]
