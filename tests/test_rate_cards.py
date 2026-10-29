"""Structured rate authority: invalid values fail; unknown rates stay unknown."""

import copy
import json

import pytest

from app.core.config import settings
from app.kb.rates import load_rate_cards
from app.tools.calc_cost import calc_cost
from app.tools.errors import ToolInputError


@pytest.fixture
def card():
    return {"rate_cards": {"company-v1": {"unit": "KRW/MD", "vat_included": False,
        "rates": {"BE": {"cost": 490000, "price": 700000}}}}}


def install(card, tmp_path, monkeypatch):
    path = tmp_path / "rates.json"
    path.write_text(json.dumps(card))
    monkeypatch.setattr(settings, "rate_card_file", path)
    return path


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf"), "700000", True, {}, []])
def test_invalid_prices_never_calculate(card, tmp_path, monkeypatch, value):
    card["rate_cards"]["company-v1"]["rates"]["BE"]["price"] = value
    install(card, tmp_path, monkeypatch)
    with pytest.raises(ToolInputError):
        calc_cost([{"role": "BE", "md": 1}], "company-v1")


@pytest.mark.parametrize("change", [{"unit": "KRW/hour"}, {"currency": "USD"}, {"vat_included": True}, {"vat_included": 0}, {"vat_included": "false"}])
def test_wrong_units_or_vat_are_rejected(card, tmp_path, monkeypatch, change):
    card["rate_cards"]["company-v1"].update(change)
    path = install(card, tmp_path, monkeypatch)
    with pytest.raises(ToolInputError):
        load_rate_cards(path)


@pytest.mark.parametrize("change", [{"unit": "USD/hour"}, {"currency": "USD"}, {"vat_included": True}])
def test_role_metadata_cannot_override_card_basis(card, tmp_path, monkeypatch, change):
    card["rate_cards"]["company-v1"]["rates"]["BE"].update(change)
    with pytest.raises(ToolInputError):
        load_rate_cards(install(card, tmp_path, monkeypatch))


@pytest.mark.parametrize("raw", ['{"rate_cards": {}, "rate_cards": {}}', '{"rate_cards": NaN}', '{"rate_cards": Infinity}'])
def test_duplicate_fields_and_non_json_numbers_are_rejected(tmp_path, raw):
    path = tmp_path / "rates.json"
    path.write_text(raw)
    with pytest.raises(ToolInputError):
        load_rate_cards(path)


def test_synthetic_metadata_is_boolean(card, tmp_path, monkeypatch):
    card["rate_cards"]["company-v1"]["synthetic"] = "true"
    with pytest.raises(ToolInputError):
        load_rate_cards(install(card, tmp_path, monkeypatch))


@pytest.mark.parametrize("mutation", ["missing-role", "null-price", "missing-price"])
def test_missing_positive_rates_need_input_without_partial_totals(card, tmp_path, monkeypatch, mutation):
    rates = card["rate_cards"]["company-v1"]["rates"]
    rates["FE"] = copy.deepcopy(rates["BE"])
    if mutation == "missing-role":
        del rates["BE"]
    elif mutation == "null-price":
        rates["BE"]["price"] = None
    else:
        del rates["BE"]["price"]
    install(card, tmp_path, monkeypatch)
    result = calc_cost([{"role": "BE", "md": 1}, {"role": "FE", "md": 1}], "company-v1")
    assert result == {"status": "NEEDS_INPUT", "missing": ["rate:BE"], "totals": None}
    assert calc_cost([{"role": "BE", "md": 0}], "company-v1")["price"] == 0


@pytest.mark.parametrize("md", ["1", "invalid", True, float("nan"), float("inf"), -1, 0.25])
def test_invalid_effort_fails_as_tool_input(card, tmp_path, monkeypatch, md):
    install(card, tmp_path, monkeypatch)
    with pytest.raises(ToolInputError):
        calc_cost([{"role": "BE", "md": md}], "company-v1")
