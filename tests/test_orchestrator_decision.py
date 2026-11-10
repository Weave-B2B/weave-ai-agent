import json

import httpx
import pytest

from app.llm.fake import FakeLlm
from app.llm.openai_responses import OpenAIResponsesLlm
from app.orchestration.decision import InvalidDecision, choose_action


def test_single_candidate_still_uses_model_and_preserves_focus():
    llm = FakeLlm([{"action": "assess_scope", "args": {"item_ids": ["R1"], "focus": "사진 조항"}, "reason": "계약 확인"}])
    decision = choose_action(llm, state_summary={"needs": ["근거 부족"]}, allowed_actions={"assess_scope"}, item_ids={"R1"})
    assert decision.action == "assess_scope" and decision.args["focus"] == "사진 조항"
    assert len(llm.calls) == 1


def test_invalid_choice_retried_and_cannot_escape_allowed_actions():
    llm = FakeLlm([{"action": "estimate_impact", "args": {"item_ids": ["R1"]}, "reason": "산정"}] * 2)
    with pytest.raises(InvalidDecision):
        choose_action(llm, state_summary={}, allowed_actions={"assess_scope"}, item_ids={"R1"})
    assert len(llm.calls) == 2


def test_foreign_item_is_rejected_before_execution():
    llm = FakeLlm([{"action": "assess_scope", "args": {"item_ids": ["OTHER"]}, "reason": "조사"}] * 2)
    with pytest.raises(InvalidDecision):
        choose_action(llm, state_summary={}, allowed_actions={"assess_scope"}, item_ids={"R1"})


def test_scope_choice_with_quote_item_is_corrected_before_dispatch():
    llm = FakeLlm([
        {"action": "assess_scope", "args": {"item_ids": ["R1", "R2"]}, "reason": "범위 확인"},
        {"action": "assess_scope", "args": {"item_ids": ["R1"]}, "reason": "기능 범위 확인"},
    ])
    decision = choose_action(llm, state_summary={}, allowed_actions={"assess_scope", "link_quote"},
                             item_ids={"R1", "R2"}, scope_item_ids={"R1"})
    assert decision.args["item_ids"] == ["R1"]
    assert "견적 문의는 link_quote로 연결" in llm.calls[1]["user"]


def test_openai_receives_closed_supervisor_argument_schemas():
    def check_closed_objects(schema):
        if schema.get("type") == "object":
            assert schema.get("additionalProperties") is False
            assert set(schema.get("required", [])) == set(schema.get("properties", {}))
        for key in ("properties", "$defs"):
            for nested in schema.get(key, {}).values():
                check_closed_objects(nested)
        for nested in schema.get("anyOf", []):
            check_closed_objects(nested)
        if "items" in schema:
            check_closed_objects(schema["items"])

    def respond(request):
        output_format = json.loads(request.content)["text"]["format"]
        assert output_format["strict"] is True
        check_closed_objects(output_format["schema"])
        output = {"action": "estimate_impact", "args": {"item_ids": ["R1"]}, "reason": "산정"}
        return httpx.Response(200, json={
            "status": "completed",
            "output": [{"content": [{"type": "output_text", "text": json.dumps(output)}]}],
        })

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        llm = OpenAIResponsesLlm(model_id="test-model", api_key="test-key", client=client)
        decision = choose_action(llm, state_summary={}, allowed_actions={"estimate_impact"}, item_ids={"R1"})

    assert decision.args == {"item_ids": ["R1"]}


def test_reassessing_already_assessed_item_requires_focus():
    llm = FakeLlm([
        {"action": "assess_scope", "args": {"item_ids": ["R1"]}, "reason": "단순 재조사"},
        {"action": "assess_scope", "args": {"item_ids": ["R1"], "focus": "첨부파일 용량 제한 조항"}, "reason": "구체 조항 재조사"},
    ])
    decision = choose_action(
        llm,
        state_summary={"answer_pending_review": False},
        allowed_actions={"assess_scope", "hold"},
        item_ids={"R1"},
        scope_item_ids={"R1"},
        assessed_item_ids={"R1"},
    )
    assert decision.action == "assess_scope"
    assert decision.args["focus"] == "첨부파일 용량 제한 조항"
    assert "focus" in llm.calls[1]["user"]
