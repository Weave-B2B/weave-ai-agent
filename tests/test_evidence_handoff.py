import pytest
from app.agents.agent3.contracts import Agent3Need
from app.orchestration.evidence_handoff import propose_scope_research


def need(**kwargs):
    return Agent3Need(**({"kind": "more_evidence", "item_ids": ["C-1"],
                          "topic": "리뷰 변경합의서 확인", "source": "agent3"} | kwargs))


def test_proposal_uses_existing_agent2_contract_and_does_not_expose_context():
    action = propose_scope_research(need(), {"C-1", "C-2"}).model_dump()
    assert action == {"action": "assess_scope", "args": {"item_ids": ["C-1"], "focus": "리뷰 변경합의서 확인"},
                      "reason": "산정에 필요한 계약 근거를 추가 확인합니다."}


def test_rejects_foreign_duplicate_or_non_research_needs():
    for n in (need(item_ids=["OTHER"]), need(item_ids=["C-1", "C-1"]),
              need(kind="client_input", target="pm")):
        with pytest.raises(ValueError):
            propose_scope_research(n, {"C-1"})
