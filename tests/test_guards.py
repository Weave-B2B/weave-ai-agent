from conftest import split_rows
from schema.schemas import Agent2Output, Agent3Output
from app.agents.agent2.guards import apply_guards
from app.agents.agent3.guards import OutputInvalid, finalize_estimates, reject_llm_amounts
import pytest


def test_a2_guards_keep_all_valid_gold_unchanged():
    for g in split_rows("agent2", "gold"):
        out = apply_guards(Agent2Output.model_validate(g))
        for a_new, a_old in zip(out.assessments, g["assessments"]):
            assert a_new.label == a_old["label"]
            assert all(e.verified for e in a_new.evidence)


def _first_with_evidence():
    for g in split_rows("agent2", "gold"):
        for a in g["assessments"]:
            if a["label"] == "out_of_scope" and a["evidence"]:
                return g, a
    raise AssertionError


def test_a2_guard_downgrades_tampered_quote_to_unclear():
    g, a = _first_with_evidence()
    bad = Agent2Output.model_validate(g).model_dump()
    tgt = next(x for x in bad["assessments"] if x["item_id"] == a["item_id"])
    for e in tgt["evidence"]:
        e["quote"] = "원문에 없는 문장입니다"
    out = apply_guards(Agent2Output.model_validate(bad))
    r = next(x for x in out.assessments if x.item_id == a["item_id"])
    assert r.label == "unclear" and r.evidence == [] and r.missing_info      # 근거 없음 ≠ 범위 밖


def test_a2_guard_drops_other_project_evidence():
    g, a = _first_with_evidence()
    bad = Agent2Output.model_validate(g).model_dump()
    tgt = next(x for x in bad["assessments"] if x["item_id"] == a["item_id"])
    other = "FSP-B-001-v1.0" if "-A-" in tgt["evidence"][0]["doc_id"] else "FSP-A-001-v1.0"
    for e in tgt["evidence"]:
        e["doc_id"] = other
    out = apply_guards(Agent2Output.model_validate(bad))
    assert next(x for x in out.assessments if x.item_id == a["item_id"]).label == "unclear"


def test_a3_finalize_recomputes_totals_and_ignores_gold_totals():
    n = 0
    from conftest import jl, EVAL
    for g in jl(EVAL / "agent3" / "gold.jsonl"):
        out = Agent3Output.model_validate(g)
        if not any(e.totals for e in out.estimates):
            continue
        fixed = finalize_estimates(out, out.estimates[0].rate_card_id)
        assert [e.totals for e in fixed.estimates] == [e.totals for e in out.estimates]
        assert all(e.totals_source == "calc_cost" for e in fixed.estimates if e.status == "draft")
        n += 1
    assert n > 5


def test_a3_missing_rate_becomes_needs_input_not_partial_total():
    from conftest import jl, EVAL
    g = next(x for x in jl(EVAL / "agent3" / "gold.jsonl") if x["status"] == "IMPACT_DRAFT_READY")
    out = finalize_estimates(Agent3Output.model_validate(g), "RC-SYNTH-2026H2-noQA") if any(
        l["role"] == "QA" and l["md"] > 0 for e in g["estimates"] for l in e["lines"]) else None
    if out is None:
        pytest.skip("QA 산정이 없는 사례")
    assert out.status == "NEEDS_INPUT" and all(e.totals is None for e in out.estimates) and out.draft is None
    assert any(q.blocking for q in out.questions)


def test_llm_amounts_are_rejected():
    with pytest.raises(OutputInvalid): reject_llm_amounts({"estimates": [{"totals": {"cost": 1}}]})
    with pytest.raises(OutputInvalid): reject_llm_amounts({"estimates": [], "draft": {"cost": "960,000원"}})
    reject_llm_amounts({"estimates": [{"totals": None}], "draft": {"cost": None}})
