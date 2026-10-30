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
        assert approved.status_code == 200 and approved.json()['draft'] is not None
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
@pytest.mark.parametrize('provider', ['gemini', 'bedrock'])
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
