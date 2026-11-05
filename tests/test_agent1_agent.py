from datetime import date

import pytest

from conftest import split_rows
from app.agents.base import AgentFailure, AgentSet
from app.agents.fixture import FixtureAgent1, FixtureAgent2, FixtureAgent3, load_chain
from app.agents.agent1.agent import LlmAgent1
from app.agents.agent1.prompts import (
    PROMPT_VERSION,
    SYSTEM,
    available_prompt_versions,
    build_user_prompt,
    load_prompt,
)
from app.core.config import Settings
from app.llm.fake import FakeLlm
from app.orchestration.engine import Engine
from app.orchestration.state_store import InMemoryStore
from schema.schemas import Agent1Input, AgentError, Answer


def _input(message_text: str = "고객 원문 비밀 요청입니다.") -> Agent1Input:
    """Create an isolated Agent1 request without dataset-specific identifiers."""
    return Agent1Input.model_validate(
        {
            "context": {
                "project_id": "P-TEST",
                "case_id": "CASE-TEST",
                "request_revision": 1,
                "analysis_id": "ANALYSIS-TEST",
            },
            "received_at": "2026-10-28T10:00:00+09:00",
            "sender_role": "고객",
            "subject": "새 기능",
            "message_text": message_text,
        }
    )


def _draft_payload(
    *,
    text: str = "새 기능을 추가합니다.",
    quote: str = "고객 원문 비밀 요청입니다.",
) -> dict:
    """Return one valid serialized Agent1Draft for FakeLlm."""
    return {
        "items": [
            {
                "item_type": "function",
                "intent": "change_request",
                "text": text,
                "quotes": [quote],
                "inferred": False,
            }
        ],
        "questions": [],
    }


def test_llm_agent1_does_not_enable_document_search_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.tools import doc_tools

    def unexpected_search(*_args, **_kwargs):
        raise AssertionError("Agent1 must not call document search")

    monkeypatch.setattr(doc_tools, "search_docs", unexpected_search)
    inp = _input().model_copy(
        update={"context": _input().context.model_copy(update={"baseline_id": "BASE-TEST"})}
    )
    llm = FakeLlm([_draft_payload()])
    agent = LlmAgent1(llm)

    output = agent.run(inp)

    assert agent.feature_search is None
    assert output.items[0].feature_candidates == []


def test_llm_agent1_returns_valid_output_and_redacted_metadata() -> None:
    inp = _input()
    llm = FakeLlm([_draft_payload()])
    agent = LlmAgent1(llm, feature_search=lambda _text, _baseline: [])

    output = agent.run(inp)

    assert output.context == inp.context
    assert output.items[0].item_id == "CASE-TEST-1"
    assert output.items[0].spans[0].text == inp.message_text
    assert output.meta.model == "fake:fake"
    assert output.meta.latency_ms is not None
    assert output.meta.agent_version == "agent1-0.2.0"
    assert output.meta.debug["prompt_version"] == PROMPT_VERSION
    assert "input_tokens" in output.meta.debug
    assert "output_tokens" in output.meta.debug
    assert inp.message_text not in str(output.meta.debug)
    assert output.meta.cost_usd is None


def test_agent1_calls_fake_once_with_internal_schema_and_safe_prompt() -> None:
    inp = _input()
    inp = inp.model_copy(
        update={
            "answers": [
                Answer(
                    answer_id="ANSWER-TEST",
                    question_id="QUESTION-TEST",
                    text="답변 내용",
                    answered_by="pm",
                    answered_at="2026-10-28T10:01:00+09:00",
                )
            ]
        }
    )
    llm = FakeLlm([_draft_payload()])

    LlmAgent1(llm, feature_search=lambda _text, _baseline: []).run(inp)

    assert len(llm.calls) == 1
    assert llm.calls[0]["schema"] == "Agent1Draft"
    assert llm.calls[0]["tool_name"] == "submit_items"
    prompt = llm.calls[0]["user"]
    assert "<message_text>\n" + inp.message_text + "\n</message_text>" in prompt
    assert "question_id" in prompt and "QUESTION-TEST" in prompt
    assert "답변 내용" in prompt
    assert "CASE-TEST" not in prompt
    assert "gold" not in prompt
    assert "OTHER-CASE" not in prompt
    assert llm.calls[0]["system"] == SYSTEM
    assert "<message_text>" in build_user_prompt(inp)


def test_agent1_loads_versioned_prompt_files() -> None:
    assert available_prompt_versions() == ("a1-draft-v0", "a1-draft-v1")

    v0 = load_prompt("a1-draft-v0")
    v1 = load_prompt("a1-draft-v1")

    assert "월말" not in v0
    assert "deadline 추론" in v1
    assert "원문 문장을 그대로 옮기지 말고" in v1
    assert "짧게 \"견적 요청\"" in v1
    assert "선택 요청은 우선순위가 낮은 부가 요청" in v1
    assert "사진 최대 3장(선택 사항)" in v1
    assert "(가능)" in v1
    assert "견적 항목은 Agent 3이 산출할 요청" in v1
    assert "question 항목(intent=estimate_request)" in v1
    assert load_prompt(PROMPT_VERSION) == SYSTEM

    with pytest.raises(ValueError, match="Unknown Agent 1 prompt version"):
        load_prompt("a1-draft-v999")


def test_agent1_prompt_version_can_be_selected_by_environment(monkeypatch) -> None:
    monkeypatch.setenv("AGENT1_PROMPT_VERSION", "a1-draft-v0")

    assert load_prompt() == load_prompt("a1-draft-v0")

    with pytest.raises(ValueError, match="Unknown Agent 1 prompt version"):
        monkeypatch.setenv("AGENT1_PROMPT_VERSION", "a1-draft-v999")
        load_prompt()


def test_agent1_v0_user_prompt_has_no_v1_deadline_context() -> None:
    inp = _input("월말 정산 때문에 급합니다.")

    prompt = build_user_prompt(inp, prompt_version="a1-draft-v0")

    assert "<deadline_context>" not in prompt


def test_agent1_prompt_provides_month_end_deadline_context() -> None:
    inp = _input("월말 정산 때문에 급합니다. 주문 목록을 엑셀로 내려받게 해주세요.")

    prompt = build_user_prompt(inp, reference_date=date(2026, 11, 2))

    assert "deadline_reference_date: 2026-11-02" in prompt
    assert "month_end_date: 2026-11-30" in prompt
    assert "days_until_month_end: 28" in prompt
    assert "calendar days from deadline_reference_date to month_end_date" in prompt
    assert "월말 정산 때문에 급합니다." in prompt

    prompt_from_received_at = build_user_prompt(inp.model_copy(update={"received_at": "2026-11-02T09:00:00+09:00"}))
    assert "deadline_reference_date: 2026-11-02" in prompt_from_received_at
    assert "days_until_month_end: 28" in prompt_from_received_at


def test_agent_failure_from_client_is_propagated_unchanged() -> None:
    failure = AgentFailure(
        AgentError(
            code="MODEL_TIMEOUT",
            message="timeout",
            stage=None,
            retryable=True,
        )
    )
    agent = LlmAgent1(FakeLlm([failure]), feature_search=lambda _text, _baseline: [])

    with pytest.raises(AgentFailure) as caught:
        agent.run(_input())

    assert caught.value is failure


def test_invalid_draft_is_reported_by_llm_client_as_output_invalid() -> None:
    llm = FakeLlm(
        [
            {"items": [], "unknown_field": "secret-output"},
            {"items": [], "unknown_field": "secret-output"},
        ]
    )
    agent = LlmAgent1(llm, feature_search=lambda _text, _baseline: [])

    with pytest.raises(AgentFailure) as caught:
        agent.run(_input())

    assert caught.value.error.code == "OUTPUT_INVALID"
    assert caught.value.error.retryable is True
    assert "secret-output" not in caught.value.error.message
    assert len(llm.calls) == 2


def test_registry_uses_llm_for_agent1_and_preserves_fixture_mode(monkeypatch) -> None:
    import app.agents.registry as registry
    from app.agents.agent1.agent import LlmAgent1 as PublicLlmAgent1
    from app.llm.client import FakeLlm as PublicFakeLlm
    from app.agents.fixture import FixtureAgent1 as PublicFixtureAgent1

    fake = PublicFakeLlm([_draft_payload()])
    monkeypatch.setattr(
        registry,
        "settings",
        Settings(_env_file=None, agent_mode="llm", llm_provider="fake"),
    )
    monkeypatch.setattr("app.llm.client.build_llm", lambda _settings: fake)

    llm_agents = registry.build_agents()

    assert isinstance(llm_agents.agent1, PublicLlmAgent1)
    assert llm_agents.agent1.llm is fake

    monkeypatch.setattr(
        registry,
        "settings",
        Settings(_env_file=None, agent_mode="fixture", llm_provider="invalid"),
    )
    fixture_agents = registry.build_agents()
    assert isinstance(fixture_agents.agent1, PublicFixtureAgent1)


def _draft_from_gold(gold: dict) -> dict:
    """Create a FakeLlm draft from one gold output for engine integration."""
    return {
        "items": [
            {
                "item_type": item["item_type"],
                "intent": item["intent"],
                "text": item["text"],
                "quotes": [span["text"] for span in item["spans"]],
                "inferred": item.get("inferred", False),
            }
            for item in gold["items"]
        ],
        "questions": [],
    }


def test_engine_start_uses_llm_agent1_then_reaches_pm_items(chain) -> None:
    row = next(
        row
        for row in split_rows("agent1", "gold")
        if row["status"] == "READY"
    )
    input_row = next(
        candidate
        for candidate in split_rows("agent1", "input")
        if candidate["context"]["case_id"] == row["context"]["case_id"]
    )
    inp = Agent1Input.model_validate(input_row)
    llm = FakeLlm([_draft_from_gold(row)])
    agent1 = LlmAgent1(llm, feature_search=lambda _text, _baseline: [])
    chain_by_id = {entry["case_id"]: entry for entry in chain}
    engine = Engine(
        AgentSet(
            agent1=agent1,
            agent2=FixtureAgent2(chain_by_id),
            agent3=FixtureAgent3(chain_by_id),
        ),
        InMemoryStore(),
    )

    record = engine.start(inp, "RC-SYNTH-2026H2-v1")

    assert record.state.stage == "pm_items"
    assert record.a1 is not None
    assert len(llm.calls) == 1
