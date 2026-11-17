"""③ 호출 전 검사: 데이터셋의 계약 케이스 13건과 chain 30건으로 검증."""
from conftest import EVAL, jl
from app.orchestration.preflight import preflight_a3


def test_contract_cases_match_expected_gate():
    for c in jl(EVAL / "agent3" / "contract_cases.jsonl"):
        gate = preflight_a3(c["input"])
        if c["invoke_agent3"]:
            assert gate is None, (c["fixture_id"], gate)
        else:
            assert gate is not None and gate.code == c["expected_gate"], (c["fixture_id"], gate, c["expected_gate"])


def test_every_chain_a3_input_is_callable(chain):
    for r in chain:
        assert preflight_a3(r["agent3_input"]) is None, r["case_id"]


def test_pending_review_waits_instead_of_calling():
    # FX-01: 검토 전이면 오류가 아니라 대기
    c = next(c for c in jl(EVAL / "agent3" / "contract_cases.jsonl") if c["fixture_id"] == "FX-01-PENDING")
    assert preflight_a3(c["input"]).code == "WAITING_REVIEW"


def test_accepts_backend_uuid_item_ids():
    import copy
    from uuid import uuid4

    c = next(c for c in jl(EVAL / "agent3" / "contract_cases.jsonl") if c["invoke_agent3"])
    raw = copy.deepcopy(c["input"])
    case_uuid = str(uuid4())
    id_map = {it["item_id"]: str(uuid4()) for it in raw["items"]}
    raw["context"]["case_id"] = case_uuid
    raw["a2_result"]["context"]["case_id"] = case_uuid
    for it in raw["items"]:
        it["case_id"] = case_uuid
        it["item_id"] = id_map[it["item_id"]]
    for cst in raw.get("constraints", []):
        cst["case_id"] = case_uuid
        cst["item_id"] = str(uuid4())
    for a in raw["a2_result"]["assessments"]:
        a["item_id"] = id_map[a["item_id"]]
    for q in raw["a2_result"].get("questions", []):
        q["case_id"] = case_uuid
        q["item_ids"] = [id_map.get(i, i) for i in q["item_ids"]]
    for r in raw["pm_review"]["items"]:
        r["item_id"] = id_map[r["item_id"]]

    assert preflight_a3(raw) is None
