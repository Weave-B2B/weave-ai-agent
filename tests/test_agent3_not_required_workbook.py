"""Workbook freshness must apply to estimates, not unrelated status replies."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.tools import impact_workbook
from app.tools.errors import ToolInputError
from app.agents.agent3.orchestration import Agent3Orchestration, DraftDocuments
from test_agent3_agent import a3_input, a3_payload
from test_agent3_orchestration import provisional, tools, review


def output(**changes):
    values = dict(context=SimpleNamespace(project_id="project"),
                  meta=SimpleNamespace(debug={}), status="NOT_REQUIRED",
                  estimates=[], pricing=None, impact_workbook=None)
    values.update(changes)
    return SimpleNamespace(**values)


def test_unestimated_not_required_reply_does_not_query_workbook(monkeypatch):
    source = Mock(return_value={"documentVersionId": "approved", "sha256": "a" * 64})
    monkeypatch.setattr(impact_workbook, "current_source", source)
    impact_workbook.assert_workbook_current(output())
    source.assert_not_called()


@pytest.mark.parametrize("changes", [
    {"status": "IMPACT_DRAFT_READY"},
    {"status": "NEEDS_INPUT"},
    {"estimates": [object()]},
    {"pricing": {"input_hash": "saved"}},
    {"plan_detail": {"plan": {"estimated_end_date": "2026-12-01"}}},
    {"schedule_detail": {"business_days": 2}},
    {"impact_workbook": {"source": {"documentVersionId": "old", "sha256": "old"}}},
    {"meta": SimpleNamespace(debug={"impact_workbook": {"source": {"documentVersionId": "old", "sha256": "old"}}})},
])
def test_not_required_label_cannot_bypass_workbook_for_consumed_data(monkeypatch, changes):
    source = Mock(return_value={"documentVersionId": "new", "sha256": "new"})
    monkeypatch.setattr(impact_workbook, "current_source", source)
    with pytest.raises(ToolInputError, match="재산정"):
        impact_workbook.assert_workbook_current(output(**changes))
    source.assert_called_once_with("project")


@pytest.mark.parametrize("legacy", [False, True])
def test_not_required_with_matching_recorded_source_is_still_verified(monkeypatch, legacy):
    reference = {"source": {"documentVersionId": "same", "sha256": "a" * 64}}
    out = output(meta=SimpleNamespace(debug={"impact_workbook": reference})) if legacy else output(impact_workbook=reference)
    source = Mock(return_value=reference["source"])
    monkeypatch.setattr(impact_workbook, "current_source", source)
    impact_workbook.assert_workbook_current(out)
    source.assert_called_once_with("project")


@pytest.mark.parametrize("scenario", ["status_check", "confirmation"])
def test_status_reply_drafts_restores_and_reviews_with_an_approved_workbook(tools, provisional, monkeypatch, scenario):
    item = provisional.items[0].model_copy(update={"item_type": "question", "intent": "status_check", "change_kind": None})
    provisional.items = [item]
    provisional.target_item_ids = []
    if scenario == "confirmation":
        item.item_type, item.intent = "function", "confirmation"
        provisional.target_item_ids = [item.item_id]
        provisional.a2_result.assessments = [provisional.a2_result.assessments[0]]
        provisional.a2_result.assessments[0].label = "in_scope"
    else:
        provisional.a2_result.assessments = []
    provisional.a2_result.questions = []
    source = Mock(return_value={"documentVersionId": "approved", "sha256": "a" * 64})
    monkeypatch.setattr(impact_workbook, "current_source", source)

    view = tools.estimate_impact(provisional)
    assert view["impact"]["status"] == "NOT_REQUIRED"
    assert view["impact"]["estimates"] == []
    assert view["standards_current"] is True
    drafted = tools.draft_documents(view["session_id"], DraftDocuments(state_revision=view["state_revision"], kind="scope_reply"))
    assert drafted["phase"] == "WAITING_FINAL_REVIEW"
    assert set(drafted["documents"]) == {"email"}
    assert "0 MD" not in drafted["documents"]["email"]["body"]
    assert drafted["can_final_review"] is True
    checkpoint = tools.export_session(drafted["session_id"])

    restored = Agent3Orchestration(tools.agent, draft_llm=tools.draft_llm)
    restored.restore_session(drafted["session_id"], checkpoint,
        case_id=provisional.context.case_id, state_revision=drafted["state_revision"], snapshot_hash=drafted["snapshot_hash"])
    current = restored.get(drafted["session_id"])
    approved = restored.review(drafted["session_id"], review(current))
    assert approved["phase"] == "FINAL_APPROVED"
    assert approved["approval_valid"] is True
    assert not tools.agent.llm.calls
    source.assert_not_called()


def test_status_reply_http_draft_succeeds_but_stale_revision_still_conflicts(tools, provisional, monkeypatch):
    from fastapi.testclient import TestClient
    from app.api import agent3_routes
    from app.main import app

    item = provisional.items[0].model_copy(update={"item_type": "question", "intent": "status_check", "change_kind": None})
    provisional.items = [item]
    provisional.target_item_ids = []
    provisional.a2_result.assessments = []
    provisional.a2_result.questions = []
    source = Mock(return_value={"documentVersionId": "approved", "sha256": "a" * 64})
    monkeypatch.setattr(impact_workbook, "current_source", source)
    monkeypatch.setattr(agent3_routes, "get_orchestration", lambda: tools)
    with TestClient(app) as http:
        started = http.post("/agent3/orchestration/impacts", json=provisional.model_dump(mode="json"))
        assert started.status_code == 200
        view = started.json()
        path = f"/agent3/orchestration/{view['session_id']}"
        request = {"state_revision": view["state_revision"], "kind": "scope_reply"}
        drafted = http.post(path + "/documents", json=request)
        assert drafted.status_code == 200
        assert drafted.json()["phase"] == "WAITING_FINAL_REVIEW"
        assert http.get(path).json()["standards_current"] is True
        assert http.post(path + "/documents", json=request).status_code == 409
    source.assert_not_called()
