"""OpenAI Responses transport and provider-key isolation without live requests."""
import json
import httpx
import pytest
from pydantic import BaseModel, ConfigDict

from app.llm.openai_responses import OpenAIResponsesLlm
from app.agents.base import AgentFailure
from app.core.config import Settings
from app.llm.factory import build_llm
from app.llm.local import LocalLlm


class Reply(BaseModel):
    model_config = ConfigDict(extra='forbid')
    text: str


def test_responses_request_and_structured_output():
    calls = []
    def handler(request):
        calls.append(request)
        body = json.loads(request.content)
        assert str(request.url) == 'https://api.openai.com/v1/responses'
        assert request.headers['authorization'] == 'Bearer test-openai-key'
        assert body['model'] == 'gpt-6-luna' and body['store'] is False
        assert body['reasoning'] == {'effort': 'none'}
        assert body['text']['format']['strict'] is True
        assert body['text']['format']['schema']['required'] == ['text']
        return httpx.Response(200, json={'status': 'completed', 'output': [{'type': 'message',
            'content': [{'type': 'output_text', 'text': '{"text":"ok"}'}]}],
            'usage': {'input_tokens': 10, 'output_tokens': 4}})
    llm = OpenAIResponsesLlm(model_id='gpt-6-luna', api_key='test-openai-key',
        client=httpx.Client(transport=httpx.MockTransport(handler)))
    result = llm.complete_structured(system='system', user='user', schema=Reply)
    assert result.parsed.text == 'ok' and result.label == 'openai:gpt-6-luna'
    assert result.usage.input_tokens == 10 and len(calls) == 1
    llm.client.close()


def test_failure_does_not_leak_key_and_does_not_fallback():
    def handler(request):
        return httpx.Response(401, json={'error': 'test-openai-key'})
    llm = OpenAIResponsesLlm(model_id='gpt-6-luna', api_key='test-openai-key',
        client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(AgentFailure) as error:
        llm.complete_structured(system='system', user='user', schema=Reply)
    assert 'test-openai-key' not in str(error.value)
    llm.client.close()


def test_factory_routes_keys_to_correct_provider():
    g = build_llm(Settings(_env_file=None, llm_provider='local', llm_model='gemini',
        llm_base_url='https://generativelanguage.googleapis.com/v1beta/openai/',
        gemini_api_key='google-key', llm_api_key='openai-key'))
    o = build_llm(Settings(_env_file=None, llm_provider='openai', llm_model='gpt-6-luna',
        gemini_api_key='google-key', llm_api_key='openai-key'))
    assert isinstance(g, LocalLlm) and g.api_key == 'google-key'
    assert isinstance(o, OpenAIResponsesLlm) and o.api_key == 'openai-key'
    g.client.close(); o.client.close()


def test_agent3_impact_and_approved_email_through_responses_api(a3_input, a3_payload):
    from app.agents.agent3.agent import LlmAgent3
    from app.agents.agent3.draft import Agent3ImpactDraft
    from app.agents.agent3.workflow import Agent3Workflow, ReviewImpact, GenerateDraft, FACTS_MARKER
    calls = []
    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        name = body['text']['format']['name']
        payload = (Agent3ImpactDraft.model_validate(a3_payload).model_dump(mode='json')
            if name == 'submit_impact' else {'narrative': f'안녕하세요. 검토 내용을 전달드립니다.\n\n{FACTS_MARKER}\n\n검토 후 회신 부탁드립니다. 감사합니다.'})
        return httpx.Response(200, json={'status': 'completed', 'output': [{'type': 'message',
            'content': [{'type': 'output_text', 'text': json.dumps(payload, ensure_ascii=False)}]}]})
    llm = OpenAIResponsesLlm(model_id='gpt-6-luna', api_key='test-openai-key',
        client=httpx.Client(transport=httpx.MockTransport(handler)))
    flow = Agent3Workflow(LlmAgent3(llm))
    view = flow.prepare(a3_input)
    approved = flow.review(view['session_id'], ReviewImpact(state_revision=view['state_revision'],
        target_result_id=view['impact']['meta']['result_id'], snapshot_hash=view['snapshot_hash'],
        request_id='test', reviewer_ref='test', approved=True, acknowledge_unknown_schedule=True))
    result = flow.generate_draft(view['session_id'], GenerateDraft(state_revision=approved['state_revision'],
        review_id=approved['approval']['review_id']))
    assert result['draft_model'] == 'openai:gpt-6-luna'
    assert '5,150,000원' in result['draft']['email_body']
    assert [call['text']['format']['name'] for call in calls] == ['submit_impact', 'submit_negotiation_draft']
    assert 'test-openai-key' not in json.dumps(calls)
    llm.client.close()


from test_agent3_agent import a3_input, a3_payload  # noqa: E402,F401
