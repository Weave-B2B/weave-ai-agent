"""Local developer controls preserve the Agent 3 approval flow."""
import copy
import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from prototype.lab import router, sessions, current
import pytest


def test_local_lab_fake_edit_approval_and_stale_session():
    app = FastAPI()
    app.include_router(router)
    sessions.clear()
    current.clear()
    input_data = json.loads(Path('prototype/data/agent3-input.json').read_text())
    with TestClient(app) as client:
        default = client.get('/prototype-lab/defaults').json()
        fake = copy.deepcopy(default['fake_response'])
        fake['estimates'][0]['lines'][0]['md'] = 1
        response = client.post('/prototype-lab/impacts', json={'input': input_data, 'provider': 'fake', 'fake_response': fake})
        assert response.status_code == 200
        view = response.json()
        assert view['impact']['estimates'][0]['lines'][0]['md'] == 0.5
        assert view['draft'] is None
        sid = view['session_id']
        review = {'state_revision': view['state_revision'], 'target_result_id': view['impact']['meta']['result_id'],
            'snapshot_hash': view['snapshot_hash'], 'request_id': 'test-review', 'reviewer_ref': 'test',
            'approved': True, 'acknowledge_unknown_schedule': True}
        approved = client.post(f'/prototype-lab/impacts/{sid}/review', json=review)
        assert approved.status_code == 200 and approved.json()['draft'] is None
        data = approved.json()
        draft = client.post(f'/prototype-lab/impacts/{sid}/draft', json={'state_revision': data['state_revision'], 'review_id': data['approval']['review_id']})
        assert draft.status_code == 200 and draft.json()['draft_model'].startswith('fake:')
        newer = client.post('/prototype-lab/impacts', json={'input': input_data, 'provider': 'fake', 'fake_response': fake})
        assert newer.status_code == 200
        assert client.post(f'/prototype-lab/impacts/{sid}/review', json=review).status_code == 409


def test_local_lab_rejects_external_origin_and_remote_local_endpoint():
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        input_data = json.loads(Path('prototype/data/agent3-input.json').read_text())
        assert client.get('/prototype-lab/defaults', headers={'Origin': 'https://example.com'}).status_code == 403
        response = client.post('/prototype-lab/impacts', json={'input': input_data, 'provider': 'local',
            'model': 'test', 'base_url': 'https://example.com/v1'})
        assert response.status_code == 422


def test_request_original_tab_uses_agent1_test_layout():
    source = Path('prototype/app.js').read_text()
    assert 'function agent1Original(item)' in source
    assert '1. 요청 원문 입력' in source
    assert '2. Agent 1 항목화 결과' in source
    assert '3. 확인 질문' in source
    assert 'state.tab === "original" ? agent1Original(item)' in source


@pytest.mark.parametrize('stage', ['agent1', 'agent2'])
def test_each_local_agent_can_run_all_sample_fixtures(stage):
    app = FastAPI()
    app.include_router(router)
    samples = json.loads(Path(f'prototype/data/{stage}-inputs.json').read_text())
    with TestClient(app) as client:
        for case_id, data in samples.items():
            response = client.post(f'/prototype-lab/{stage}', json={'input': data, 'provider': 'fake'})
            assert response.status_code == 200, (case_id, response.text)
            assert response.json()['context']['case_id'] == case_id
            assert response.json()['meta']['result_id']


@pytest.mark.parametrize('stage', ['agent1', 'agent2'])
def test_agent_lab_provider_requires_local_origin(stage):
    app = FastAPI()
    app.include_router(router)
    sample = json.loads(Path(f'prototype/data/{stage}-inputs.json').read_text())['CR-A-001']
    with TestClient(app) as client:
        response = client.post(f'/prototype-lab/{stage}', headers={'Origin': 'https://example.com'},
                               json={'input': sample, 'provider': 'fake'})
        assert response.status_code == 403


@pytest.mark.parametrize('stage', ['agent1', 'agent2'])
@pytest.mark.parametrize('provider', ['gemini', 'openai', 'bedrock'])
def test_agent_lab_passes_selected_provider(stage, provider, monkeypatch):
    import prototype.lab as lab
    captured = []
    class Agent:
        def run(self, inp):
            return (lab.FixtureAgent1 if stage == 'agent1' else lab.FixtureAgent2)(lab.load_chain()).run(inp)
    def make_client(body):
        captured.append((body.provider, body.model, body.region))
        return object()
    monkeypatch.setattr(lab, 'provider_client', make_client)
    monkeypatch.setattr(lab, 'LlmAgent1', lambda client: Agent())
    monkeypatch.setattr(lab, 'build_agent2', lambda client: Agent())
    app = FastAPI(); app.include_router(router)
    sample = json.loads(Path(f'prototype/data/{stage}-inputs.json').read_text())['CR-A-003']
    with TestClient(app) as client:
        response = client.post(f'/prototype-lab/{stage}', json={'input': sample, 'provider': provider,
                               'model': 'example-model', 'region': 'ap-northeast-2'})
        assert response.status_code == 200
        assert captured == [(provider, 'example-model', 'ap-northeast-2')]


def test_lab_uses_distinct_provider_keys_and_environment_precedence(monkeypatch, tmp_path):
    import prototype.lab as lab
    from types import SimpleNamespace
    (tmp_path / '.env').write_text('LLM_API_KEY=gpt-file-key\nGEMINI_API_KEY=gemini-file-key\n')
    monkeypatch.setattr(lab, 'ROOT', tmp_path)
    monkeypatch.delenv('LLM_API_KEY', raising=False)
    monkeypatch.delenv('GEMINI_API_KEY', raising=False)
    captured = []
    monkeypatch.setattr(lab, 'build_llm', lambda settings: captured.append(settings) or object())
    lab.provider_client(SimpleNamespace(provider='gemini', model='', region='ap-northeast-2'))
    lab.provider_client(SimpleNamespace(provider='openai', model='', region='ap-northeast-2'))
    assert captured[0].gemini_api_key == 'gemini-file-key' and captured[0].llm_api_key == ''
    assert captured[1].llm_api_key == 'gpt-file-key' and captured[1].gemini_api_key == ''
    assert captured[1].llm_model == 'gpt-6-luna' and captured[1].llm_provider == 'openai'
    monkeypatch.setenv('GEMINI_API_KEY', 'gemini-env-key')
    monkeypatch.setenv('LLM_API_KEY', 'gpt-env-key')
    lab.provider_client(SimpleNamespace(provider='gemini', model='', region='ap-northeast-2'))
    lab.provider_client(SimpleNamespace(provider='openai', model='', region='ap-northeast-2'))
    assert captured[2].gemini_api_key == 'gemini-env-key'
    assert captured[3].llm_api_key == 'gpt-env-key'


def test_lab_gpt_default_ignores_fake_server_model(monkeypatch):
    import prototype.lab as lab
    from types import SimpleNamespace
    monkeypatch.setenv('LLM_MODEL', 'fake-ui-demo')
    monkeypatch.setenv('LLM_API_KEY', 'test-key')
    captured = []
    monkeypatch.setattr(lab, 'build_llm', lambda settings: captured.append(settings) or object())
    lab.provider_client(SimpleNamespace(provider='openai', model='', region='ap-northeast-2'))
    assert captured[0].llm_model == 'gpt-6-luna'
    lab.provider_client(SimpleNamespace(provider='openai', model='custom-model', region='ap-northeast-2'))
    assert captured[1].llm_model == 'custom-model'
    assert lab.defaults(SimpleNamespace(client=SimpleNamespace(host='testclient'), headers={}))['default_models']['openai'] == 'gpt-6-luna'


def test_lab_ollama_defaults_and_model_override(monkeypatch):
    import prototype.lab as lab
    from types import SimpleNamespace
    monkeypatch.setenv('OLLAMA_BASE_URL', 'http://localhost:11434')
    monkeypatch.setenv('OLLAMA_MODEL', 'gpt-oss:20b')
    captured = []
    monkeypatch.setattr(lab, 'build_llm', lambda settings: captured.append(settings) or object())

    lab.provider_client(SimpleNamespace(provider='ollama', model='', region='ap-northeast-2'))
    lab.provider_client(SimpleNamespace(provider='ollama', model='qwen3:8b', region='ap-northeast-2'))

    assert captured[0].llm_provider == 'ollama'
    assert captured[0].ollama_base_url == 'http://localhost:11434'
    assert captured[0].ollama_model == 'gpt-oss:20b'
    assert captured[1].llm_model == 'qwen3:8b'
    assert captured[1].ollama_model == 'qwen3:8b'


def test_lab_ollama_llm_model_fallback(monkeypatch):
    import prototype.lab as lab
    from types import SimpleNamespace
    monkeypatch.setenv('OLLAMA_BASE_URL', 'http://localhost:11434')
    monkeypatch.delenv('OLLAMA_MODEL', raising=False)
    monkeypatch.setenv('LLM_MODEL', 'qwen3.5:9b')
    monkeypatch.setattr(lab, 'dotenv_values', lambda path: {})
    captured = []
    monkeypatch.setattr(lab, 'build_llm', lambda settings: captured.append(settings) or object())

    lab.provider_client(SimpleNamespace(provider='ollama', model='', region='ap-northeast-2'))

    assert captured[0].llm_model == 'qwen3.5:9b'
    assert captured[0].ollama_model == 'qwen3.5:9b'


def test_lab_installed_ollama_models(monkeypatch):
    import prototype.lab as lab
    import httpx

    class Response:
        def raise_for_status(self):
            return None
        def json(self):
            return {'models': [{'name': 'qwen3.5:9b'}, {'name': 'gpt-oss:20b'}]}

    monkeypatch.setattr(lab.httpx, 'get', lambda url, timeout: Response())

    assert lab.list_ollama_models('http://localhost:11434') == ['qwen3.5:9b', 'gpt-oss:20b']


def test_lab_ollama_read_timeout_defaults(monkeypatch):
    import prototype.lab as lab
    from types import SimpleNamespace
    monkeypatch.setenv('OLLAMA_READ_TIMEOUT_SEC', '300')
    monkeypatch.setenv('OLLAMA_CONNECT_TIMEOUT_SEC', '10')
    captured = []
    monkeypatch.setattr(lab, 'build_llm', lambda settings: captured.append(settings) or object())

    lab.provider_client(SimpleNamespace(provider='ollama', model='gpt-oss:20b', region='ap-northeast-2'))

    assert captured[0].ollama_read_timeout_sec == 300
    assert captured[0].ollama_connect_timeout_sec == 10


def test_agent3_uses_shared_provider_configuration(monkeypatch):
    import prototype.lab as lab
    from types import SimpleNamespace
    calls = []
    monkeypatch.setattr(lab, 'provider_client', lambda body: calls.append(body.provider) or object())
    class Workflow:
        def __init__(self, agent): pass
        def prepare(self, inp): return {'session_id': 'test-session'}
    monkeypatch.setattr(lab, 'Agent3Workflow', Workflow)
    app = FastAPI(); app.include_router(router)
    sample = json.loads(Path('prototype/data/agent3-input.json').read_text())
    with TestClient(app) as client:
        response = client.post('/prototype-lab/impacts', json={'input': sample, 'provider': 'gemini'})
    assert response.status_code == 200
    assert calls == ['gemini']


def test_local_baselines_and_new_fake_agent1_result_id():
    app = FastAPI(); app.include_router(router)
    sample = json.loads(Path('prototype/data/agent1-inputs.json').read_text())['CR-A-001']
    sample['context']['case_id'] = 'CR-NEW-001'
    with TestClient(app) as client:
        choices = client.get('/prototype-lab/baselines')
        assert choices.status_code == 200
        assert any(b['id'] == 'P-COM-101:BL-2' and b['as_of'] == '2026-10-05' for b in choices.json())
        assert client.get('/prototype-lab/baselines', headers={'Origin': 'https://example.com'}).status_code == 403
        result = client.post('/prototype-lab/agent1', json={'input': sample, 'provider': 'fake'})
        assert result.status_code == 200
        assert result.json()['meta']['result_id'] and result.json()['meta']['created_at']
