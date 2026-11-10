"""Run API가 쓰는 의존성 묶음. 행위자(actor)별로 Backend 클라이언트와 Runtime을 구성한다."""
from dataclasses import dataclass
from functools import lru_cache
from typing import Callable, Iterator, Optional, Protocol

from app.agents.base import AgentSet
from app.core.config import settings
from app.orchestration.http_run_state import HttpRunStateClient
from app.orchestration.run_state import (
    CommittedResult, RunInputs, RunStateSnapshot, StateChangeReceipt, SubmitInputRequest,
)
from app.orchestration.runtime import BackendRunInputProvider, OrchestrationRuntime


class RunBackend(Protocol):
    """Run API가 Backend(또는 테스트용 Fake)에 요구하는 범위. RunStateClient 밖의 create_run·submit_input을 포함한다."""
    def create_run(self, project_id: str, change_request_id: str, inputs: Optional[RunInputs] = None) -> RunStateSnapshot: ...
    def get_state(self, run_id: str) -> RunStateSnapshot: ...
    def get_result(self, run_id: str, result_id: str) -> CommittedResult: ...
    def submit_input(self, req: SubmitInputRequest) -> StateChangeReceipt: ...


@dataclass
class RunServices:
    backend: RunBackend
    runtime: OrchestrationRuntime


class BackendNotConfigured(Exception):
    pass


@lru_cache
def _agents() -> AgentSet:
    from app.agents.registry import build_agents
    return build_agents()


def build_run_services(actor_member_id: str) -> Iterator[RunServices]:
    if not settings.backend_base_url or not settings.backend_internal_token:
        raise BackendNotConfigured("backend_base_url / backend_internal_token 이 설정되지 않았다")
    client = HttpRunStateClient(settings.backend_base_url, settings.backend_internal_token, actor_member_id,
                                timeout=settings.backend_timeout_sec)
    try:
        yield RunServices(client, OrchestrationRuntime(client, _agents(), BackendRunInputProvider(client)))
    finally:
        client.close()


RunServicesFactory = Callable[[str], Iterator[RunServices]]
