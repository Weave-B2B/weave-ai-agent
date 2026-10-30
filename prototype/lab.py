"""Local-only Agent 3 developer test endpoints; credentials stay on the server."""
import json
import os
from pathlib import Path
from threading import RLock
from typing import Literal
from urllib.parse import urlsplit

from dotenv import dotenv_values
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from app.agents.agent3.agent import LlmAgent3
from app.agents.agent3.workflow import Agent3Workflow, EditImpact, ReviewImpact
from app.api.agent3_routes import _guard
from app.core.config import ROOT, Settings
from app.llm.factory import build_llm
from app.llm.fake import FakeLlm
from schema.schemas import Agent1Input, Agent2Input, Agent3Input
from app.agents.agent1.agent import LlmAgent1
from app.agents.registry import build_agent2
from app.agents.fixture import FixtureAgent1, FixtureAgent2, load_chain
from app.agents.base import AgentFailure
from app.orchestration.ids import new_result_id, now_iso

router = APIRouter(prefix='/prototype-lab', tags=['local prototype'])
lock = RLock()
sessions = {}
current = {}
GEMINI_URL = 'https://generativelanguage.googleapis.com/v1beta/openai/'

class LabInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    input: Agent3Input
    provider: Literal['fake', 'gemini', 'bedrock'] = 'fake'
    model: str = ''
    base_url: str = ''
    region: str = 'ap-northeast-2'
    fake_response: dict | None = None

class Agent1LabInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    input: Agent1Input
    provider: Literal['fake', 'gemini', 'bedrock'] = 'fake'
    model: str = ''
    region: str = 'ap-northeast-2'

class Agent2LabInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    input: Agent2Input
    provider: Literal['fake', 'gemini', 'bedrock'] = 'fake'
    model: str = ''
    region: str = 'ap-northeast-2'

def provider_client(body):
    values = dotenv_values(ROOT / '.env') if (ROOT / '.env').is_file() else {}
    key = os.environ.get('GEMINI_API_KEY') or values.get('GEMINI_API_KEY') or os.environ.get('LLM_API_KEY') or values.get('LLM_API_KEY') or ''
    if body.provider == 'gemini' and not key:
        raise HTTPException(422, '서버 .env에 GEMINI_API_KEY를 설정해 주세요.')
    model = body.model.strip() or ('gemini-2.5-flash-lite' if body.provider == 'gemini' else '')
    if not model:
        raise HTTPException(422, '모델 이름을 입력해 주세요.')
    config = Settings(_env_file=None, llm_provider='bedrock' if body.provider == 'bedrock' else 'local',
        llm_model=model, llm_base_url=GEMINI_URL if body.provider == 'gemini' else '',
        llm_api_key=key if body.provider == 'gemini' else '',
        llm_local_json_mode='schema' if body.provider == 'gemini' else 'prompt', aws_region=body.region)
    return build_llm(config)

def run_local_agent(request, body, stage):
    local_only(request)
    try:
        if body.provider == 'fake':
            agent = (FixtureAgent1 if stage == 'agent1' else FixtureAgent2)(load_chain())
        elif stage == 'agent1':
            agent = LlmAgent1(provider_client(body))
        else:
            agent = build_agent2(provider_client(body))
        out = agent.run(body.input)
        if out.context != body.input.context:
            raise ValueError('결과의 요청 정보가 입력과 일치하지 않습니다.')
        out.meta.result_id = out.meta.result_id or new_result_id(stage, body.input.context.case_id)
        out.meta.created_at = out.meta.created_at or now_iso()
        return out
    except AgentFailure as exc:
        raise HTTPException(502, exc.error.model_dump(mode='json')) from None
    except KeyError:
        raise HTTPException(404, '해당 요청의 가상 응답을 찾을 수 없습니다.') from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None

@router.post('/agent1')
def run_agent1(request: Request, body: Agent1LabInput):
    return run_local_agent(request, body, 'agent1')

@router.post('/agent2')
def run_agent2(request: Request, body: Agent2LabInput):
    return run_local_agent(request, body, 'agent2')


def local_only(request: Request):
    if not request.client or request.client.host not in ('127.0.0.1', '::1', 'testclient'):
        raise HTTPException(403, '로컬 테스트에서만 사용할 수 있습니다.')
    origin = request.headers.get('origin')
    if origin and urlsplit(origin).hostname not in ('127.0.0.1', 'localhost', '::1'):
        raise HTTPException(403, '로컬 화면에서만 사용할 수 있습니다.')


def workflow_for(sid):
    with lock:
        entry = sessions.get(sid)
        if not entry:
            raise HTTPException(404, '테스트 세션을 찾을 수 없습니다. 다시 산정해 주세요.')
        workflow, key = entry
        if current.get(key) != sid:
            raise HTTPException(409, '새 산정 결과가 있습니다. 다시 산정해 주세요.')
        return workflow


@router.get('/defaults')
def defaults(request: Request):
    local_only(request)
    fake = json.loads((ROOT / 'examples/agent3/fake_responses.json').read_text(encoding='utf-8'))[0]
    return {'fake_response': fake, 'model': 'gemini-2.5-flash-lite'}


@router.post('/impacts')
def prepare(request: Request, body: LabInput):
    local_only(request)
    if body.provider == 'fake':
        fake = body.fake_response
        if fake is None:
            fake = json.loads((ROOT / 'examples/agent3/fake_responses.json').read_text(encoding='utf-8'))[0]
        client = FakeLlm([fake], model_id='prototype-fake')
    else:
        values = dotenv_values(ROOT / '.env') if (ROOT / '.env').is_file() else {}
        key = os.environ.get('GEMINI_API_KEY') or values.get('GEMINI_API_KEY') or os.environ.get('LLM_API_KEY') or values.get('LLM_API_KEY') or ''
        if body.provider == 'gemini' and not key:
            raise HTTPException(422, '서버 .env에 GEMINI_API_KEY를 설정해 주세요.')
        base = GEMINI_URL if body.provider == 'gemini' else ''
        model = body.model.strip() or ('gemini-2.5-flash-lite' if body.provider == 'gemini' else '')
        if not model:
            raise HTTPException(422, '모델 이름을 입력해 주세요.')
        config = Settings(_env_file=None, llm_provider='bedrock' if body.provider == 'bedrock' else 'local',
            llm_model=model, llm_base_url=base, llm_api_key=key if body.provider == 'gemini' else '',
            llm_local_json_mode='schema' if body.provider == 'gemini' else 'prompt', aws_region=body.region)
        client = build_llm(config)
    workflow = Agent3Workflow(LlmAgent3(client))
    view = _guard(lambda: workflow.prepare(body.input))
    case = (body.input.context.project_id, body.input.context.case_id)
    with lock:
        sessions[view['session_id']] = (workflow, case)
        current[case] = view['session_id']
    return view


@router.get('/impacts/{sid}')
def get(request: Request, sid: str):
    local_only(request)
    return _guard(lambda: workflow_for(sid).get(sid))


@router.post('/impacts/{sid}/edit')
def edit(request: Request, sid: str, body: EditImpact):
    local_only(request)
    with lock:
        return _guard(lambda: workflow_for(sid).edit(sid, body))


@router.post('/impacts/{sid}/review')
def review(request: Request, sid: str, body: ReviewImpact):
    local_only(request)
    with lock:
        return _guard(lambda: workflow_for(sid).review(sid, body))
