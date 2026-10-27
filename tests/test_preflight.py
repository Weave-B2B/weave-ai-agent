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
