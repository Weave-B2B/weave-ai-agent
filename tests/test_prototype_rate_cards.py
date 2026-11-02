"""Rate card inspection is local-only and reads the active server data."""
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import settings
from app.agents.agent3.workflow import _rate_hash
from prototype.rate_routes import router


def test_rate_card_returns_active_rates_and_current_fingerprint(tmp_path, monkeypatch):
    path = tmp_path / 'rates.json'
    path.write_text(json.dumps({'rate_cards': {'TEST': {'synthetic': True, 'unit': 'KRW/MD',
        'vat_included': False, 'rates': {'PM': {'cost': 12, 'price': 34}}}}}))
    monkeypatch.setattr(settings, 'rate_card_file', path)
    app = FastAPI(); app.include_router(router)
    with TestClient(app) as client:
        response = client.get('/prototype-data/rate-cards/TEST')
        assert response.status_code == 200
        card = response.json()
        assert card['rates']['PM'] == {'cost': 12, 'price': 34}
        assert card['synthetic'] is True and card['fingerprint'] == _rate_hash('TEST')
        assert response.headers['cache-control'] == 'no-store'
        path.write_text(json.dumps({'rate_cards': {'TEST': {'synthetic': True, 'unit': 'KRW/MD',
            'vat_included': False, 'rates': {'PM': {'cost': 12, 'price': 99}}}}}))
        changed = client.get('/prototype-data/rate-cards/TEST').json()
        assert changed['rates']['PM']['price'] == 99 and changed['fingerprint'] != card['fingerprint']
        assert client.get('/prototype-data/rate-cards/MISSING').status_code == 404
        assert client.get('/prototype-data/rate-cards/TEST', headers={'Origin': 'https://example.com'}).status_code == 403
        assert client.get('/prototype-data/rate-cards/TEST', headers={'Host': 'example.com'}).status_code == 403


def test_rate_card_forbids_nonlocal_client():
    app = FastAPI(); app.include_router(router)
    with TestClient(app, client=('203.0.113.5', 1234)) as client:
        assert client.get('/prototype-data/rate-cards/anything').status_code == 403


def test_local_edit_creates_new_persistent_version_and_recalculates(tmp_path, monkeypatch):
    from app.kb.loader import rate_cards
    from app.tools.calc_cost import calc_cost
    source = tmp_path / 'source.json'
    original = {'rate_cards': {'TEST': {'synthetic': True, 'unit': 'KRW/MD', 'vat_included': False,
        'rates': {'PM': {'cost': 100, 'price': 200}, 'QA': {'cost': 60, 'price': 100}}}}}
    source.write_text(json.dumps(original))
    monkeypatch.setattr(settings, 'rate_card_file', source)
    monkeypatch.setattr(settings, 'data_dir', tmp_path / 'data')
    monkeypatch.setenv('PROTOTYPE_LAB', '1')
    app = FastAPI(); app.include_router(router)
    with TestClient(app, base_url='http://localhost') as client:
        card = client.get('/prototype-data/rate-cards/TEST').json()
        rates = {r: {'cost': None, 'price': None} for r in ('PM', 'UX', 'FE', 'BE', 'QA')}
        rates['PM'] = {'cost': 150, 'price': 300}
        rates['QA'] = {'cost': 60, 'price': 100}
        payload = {'expected_fingerprint': card['fingerprint'], 'reason': '단가 검토', 'rates': rates}
        path = '/prototype-data/rate-cards/TEST/versions'
        assert client.post(path, json=payload).status_code == 403
        assert client.post(path, json=payload, headers={'Origin': 'https://example.com'}).status_code == 403
        assert client.post(path, json={**payload, 'expected_fingerprint': 'old'}, headers={'Origin': 'http://localhost'}).status_code == 409
        invalid = {**payload, 'rates': {**rates, 'PM': {'cost': -1, 'price': 1}}}
        assert client.post(path, json=invalid, headers={'Origin': 'http://localhost'}).status_code == 422
        saved = client.post(path, json=payload, headers={'Origin': 'http://localhost'})
        assert saved.status_code == 200, saved.text
        new_id = saved.json()['id']
        assert new_id.startswith('RC-LOCAL-') and new_id != 'TEST'
        assert json.loads(source.read_text()) == original
        assert rate_cards()[new_id]['source_rate_card_id'] == 'TEST'
        assert calc_cost([{'role': 'PM', 'md': 2}], new_id)['price'] == 600
        assert calc_cost([{'role': 'PM', 'md': 2}], 'TEST')['price'] == 400
        assert client.get('/prototype-data/rate-cards/' + new_id).json()['rates']['PM']['price'] == 300
        assert len(list((tmp_path / '.prototype-rate-cards').glob('*.json'))) == 1
    monkeypatch.setenv('PROTOTYPE_LAB', '0')
    assert new_id not in rate_cards()


def test_edit_not_available_outside_lab(monkeypatch):
    monkeypatch.delenv('PROTOTYPE_LAB', raising=False)
    app = FastAPI(); app.include_router(router)
    rates = {r: {'cost': None, 'price': None} for r in ('PM', 'UX', 'FE', 'BE', 'QA')}
    with TestClient(app, base_url='http://localhost') as client:
        response = client.post('/prototype-data/rate-cards/test/versions',
            headers={'Origin': 'http://localhost'}, json={'expected_fingerprint': 'abc', 'reason': 'test', 'rates': rates})
        assert response.status_code == 403
