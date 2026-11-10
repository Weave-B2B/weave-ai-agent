import pytest
from app.agents.agent3.workflow import FAKE_EMAIL
from app.llm.fake import FakeLlm
from app.orchestration.email_draft import generate_email_draft
from app.orchestration.engine import InvalidTransition, VersionConflict
from schema.schemas import Answer, Question
from test_engine import drive, make


def ready_case(chain, status="IMPACT_DRAFT_READY"):
    row = next(r for r in chain if r["agent3_gold"]["status"] == status)
    engine, _ = make()
    rec = drive(engine, row)
    return engine, rec


def test_draft_uses_server_calculations_and_no_internal_cost(chain):
    engine, rec = ready_case(chain)
    llm = FakeLlm([FAKE_EMAIL])
    out = generate_email_draft(engine, rec.inp.context.case_id, rec.state.state_revision, rec.a3.meta.result_id, llm)
    draft = out["draft"]
    assert draft["impact_result_id"] == rec.a3.meta.result_id
    assert draft["status"] == "customer_unagreed_draft"
    assert "원가" not in draft["body"] and "마진" not in draft["body"]
    assert f'{rec.a3.estimates[0].totals.price:,}원' in draft["body"]
    assert rec.pm_review is not None and rec.email_draft == draft
    assert out["state_revision"] == rec.state.state_revision


def test_no_estimate_does_not_invent_zero_or_numbers(chain):
    engine, rec = ready_case(chain, "NOT_REQUIRED")
    out = generate_email_draft(engine, rec.inp.context.case_id, rec.state.state_revision, rec.a3.meta.result_id, FakeLlm([FAKE_EMAIL]))
    assert "산정하지 않았습니다" in out["draft"]["body"]
    assert "0원" not in out["draft"]["body"]


def test_multiple_estimates_do_not_present_unverified_sum(chain):
    engine, rec = ready_case(chain)
    rec.a3.estimates.append(rec.a3.estimates[0].model_copy(deep=True))
    out = generate_email_draft(engine, rec.inp.context.case_id, rec.state.state_revision, rec.a3.meta.result_id, FakeLlm([FAKE_EMAIL]))
    assert "작업 범위가 중복될 수 있어" in out["draft"]["body"]
    assert "전체 예상 공수" not in out["draft"]["body"]


def test_blocking_questions_and_stale_result_are_rejected(chain):
    engine, rec = ready_case(chain)
    q = Question(question_id="Q-EMAIL", case_id=rec.inp.context.case_id, stage="agent3", text="조건?", reason="확인", target="pm", blocking=True)
    rec.questions[q.question_id] = q
    with pytest.raises(InvalidTransition):
        generate_email_draft(engine, rec.inp.context.case_id, rec.state.state_revision, rec.a3.meta.result_id, FakeLlm([FAKE_EMAIL]))
    rec.questions.clear()
    with pytest.raises(VersionConflict):
        generate_email_draft(engine, rec.inp.context.case_id, rec.state.state_revision, "old", FakeLlm([FAKE_EMAIL]))


def test_draft_invalidated_on_new_result(chain):
    engine, rec = ready_case(chain)
    generate_email_draft(engine, rec.inp.context.case_id, rec.state.state_revision, rec.a3.meta.result_id, FakeLlm([FAKE_EMAIL]))
    engine._invalidate_from(rec, "agent3")
    assert rec.email_draft is None


def test_hallucinated_numbers_rejected(chain):
    engine, rec = ready_case(chain)
    with pytest.raises(ValueError):
        generate_email_draft(engine, rec.inp.context.case_id, rec.state.state_revision, rec.a3.meta.result_id,
                             FakeLlm([{"narrative": "추가 비용 5원입니다. [[APPROVED_FACTS]]"}]))
