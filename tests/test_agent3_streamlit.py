"""Visible Agent3 workflow, including edits and the approval gate."""

from pathlib import Path

from streamlit.testing.v1 import AppTest

from app.demo_agent3 import PREFIX, SCENARIOS

ROOT = Path(__file__).resolve().parents[1]


def page():
    app = AppTest.from_file(str(ROOT / "app/demo_agent3.py")).run(timeout=10)
    assert not app.exception
    return app


def run_impact(app):
    app.button(key=PREFIX + "run").click().run(timeout=10)
    assert not app.exception
    return app.session_state[PREFIX + "view"]


def approve(app):
    app.checkbox(key=PREFIX + "ack_schedule").check()
    app.button(key=PREFIX + "approve").click().run(timeout=10)
    assert not app.exception
    return app.session_state[PREFIX + "view"]


def test_demo_runs_repeatedly_without_exhausting_the_fake_client():
    app = page()
    assert app.title[0].value == "에이전트 3"
    first = run_impact(app)
    assert first["phase"] == "WAITING_IMPACT_APPROVAL" and first["draft"] is None
    assert [m.value for m in app.metric][:3] == ["10 MD", "6,425,000원", "4,480,000원"]
    second = run_impact(app)
    assert second["session_id"] != first["session_id"] and second["draft"] is None
    assert not app.error


def test_pm_approval_shows_draft_and_preserves_result_on_rerun():
    app = page()
    run_impact(app)
    view = approve(app)
    assert view["phase"] == "DRAFT_READY" and "6,425,000원" in view["draft"]["cost"]
    assert any("협의안 초안 · 내부 검토용" in item.value for item in app.markdown)
    draft_id = view["draft_result_id"]
    app.run()
    assert app.session_state[PREFIX + "view"]["draft_result_id"] == draft_id
    assert not app.exception


def test_schedule_acknowledgment_is_required_before_generating_draft():
    app = page()
    run_impact(app)
    app.button(key=PREFIX + "approve").click().run(timeout=10)
    assert app.error and app.session_state[PREFIX + "view"]["draft"] is None
    assert app.session_state[PREFIX + "view"]["phase"] == "WAITING_IMPACT_APPROVAL"


def test_pm_edit_recalculates_and_requires_approval_again():
    app = page()
    run_impact(app)
    approved = approve(app)
    app.number_input[0].set_value(1.5)
    app.text_input(key=PREFIX + "edit_reason").set_value("PM 공수 검토")
    app.button(key=PREFIX + "edit_submit").click().run(timeout=10)
    assert not app.exception
    edited = app.session_state[PREFIX + "view"]
    assert edited["phase"] == "WAITING_IMPACT_APPROVAL"
    assert edited["draft"] is None and edited["approval"] is None
    assert edited["impact"]["estimates"][0]["totals"]["price"] == 7175000
    assert edited["impact"]["meta"]["result_id"] != approved["impact"]["meta"]["result_id"]
    assert not app.checkbox(key=PREFIX + "ack_schedule").value


def test_missing_rate_does_not_show_a_complete_amount_or_approval():
    app = page()
    app.selectbox(key=PREFIX + "scenario").select(SCENARIOS[1]).run()
    view = run_impact(app)
    assert view["phase"] == "WAITING_INPUT" and view["draft"] is None
    assert [m.value for m in app.metric][1] == "미산정"
    assert not any(b.label == "승인하고 협의안 작성" for b in app.button)
    assert any("단가" in item.value for item in app.warning)


def test_pm_review_pending_and_no_target_scenarios_are_visible():
    app = page()
    for scenario, phase in [(SCENARIOS[2], "WAITING_UPSTREAM_REVIEW"), (SCENARIOS[3], "NOT_REQUIRED")]:
        app.selectbox(key=PREFIX + "scenario").select(scenario).run()
        view = run_impact(app)
        assert view["phase"] == phase and view["draft"] is None
        assert not app.exception


def test_changed_input_disables_old_approval_and_invalid_run_clears_result():
    app = page()
    run_impact(app)
    app.text_area(key=PREFIX + "input").set_value("{").run()
    assert app.button(key=PREFIX + "approve").disabled
    assert any("이전 결과" in item.value for item in app.warning)
    app.button(key=PREFIX + "run").click().run(timeout=10)
    assert not app.exception
    assert PREFIX + "view" not in app.session_state
    assert app.error


def test_rejection_keeps_draft_empty_and_retains_reason():
    app = page()
    run_impact(app)
    app.text_area(key=PREFIX + "review_reason").set_value("사진 기능 공수 재검토")
    app.button(key=PREFIX + "reject").click().run(timeout=10)
    assert not app.exception
    view = app.session_state[PREFIX + "view"]
    assert view["draft"] is None and view["approval"]["approved"] is False
    assert view["approval"]["reason"] == "사진 기능 공수 재검토"
