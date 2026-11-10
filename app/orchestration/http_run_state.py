"""RunStateClient의 HTTP 구현. Backend `/internal/v1/runs` API(docs/orchestration/backend-api-contract.md)를 호출한다.

AI 서버는 Run 상태의 소유자가 아니므로 이 클래스는 상태를 캐시하지 않는다. 모든 호출은 Backend 응답을 그대로 반환한다.
쓰기 요청은 operation_id로 멱등하므로 네트워크 오류·5xx는 같은 요청을 그대로 재시도할 수 있다(StatePersistenceError).
"""
from typing import Optional

import httpx

from app.orchestration.run_state import (
    CommitResultRequest, CommittedResult, InvalidStateTransition, OperationConflict, RecordEditRequest,
    RequestReviewRequest, ResolveReviewRequest, ResultNotFound, RunNotFound, RunStateError, RunStateSnapshot,
    StartAgentRequest, StateChangeReceipt, StatePersistenceError, StateRevisionConflict,
)

TOKEN_HEADER = "X-Internal-Token"
ACTOR_HEADER = "X-Actor-Member-Id"


class RunAccessDenied(RunStateError):
    """서비스 토큰 오류(401) 또는 프로젝트 접근 권한 없음(403). 재시도해도 결과가 같다."""
    code = "RUN_ACCESS_DENIED"


class RunRequestRejected(RunStateError):
    """잘못된 요청 본문(400)이거나 payload 크기 초과(413). 요청을 고치지 않으면 재시도해도 같다."""
    code = "RUN_REQUEST_REJECTED"


class HttpRunStateClient:
    """Backend Run 상태 API 클라이언트. 행위자(actor)는 이 클라이언트가 대신하는 사용자의 company member ID다."""

    def __init__(self, base_url: str, service_token: str, actor_member_id: str, *,
                 timeout: float = 10.0, http: Optional[httpx.Client] = None):
        self._actor = actor_member_id
        self._http = http or httpx.Client(base_url=base_url, timeout=timeout)
        self._headers = {TOKEN_HEADER: service_token, ACTOR_HEADER: actor_member_id}

    def close(self) -> None:
        self._http.close()

    # Protocol 밖: Run 생성은 직접/자동 실행 진입점(Backend B1)이 쓴다.
    def create_run(self, project_id: str, change_request_id: str) -> RunStateSnapshot:
        body = {"project_id": project_id, "change_request_id": change_request_id}
        return RunStateSnapshot.model_validate(self._call("POST", "/internal/v1/runs", json=body))

    def get_state(self, run_id: str) -> RunStateSnapshot:
        return RunStateSnapshot.model_validate(self._call("GET", f"/internal/v1/runs/{run_id}", run_id=run_id))

    def get_result(self, run_id: str, result_id: str) -> CommittedResult:
        data = self._call("GET", f"/internal/v1/runs/{run_id}/results/{result_id}", run_id=run_id)
        return CommittedResult.model_validate(data)

    def start_agent(self, req: StartAgentRequest) -> StateChangeReceipt:
        return self._write(req, "agents/start")

    def commit_result(self, req: CommitResultRequest) -> StateChangeReceipt:
        return self._write(req, "results")

    def record_edit(self, req: RecordEditRequest) -> StateChangeReceipt:
        return self._write(req, "edits")

    def request_review(self, req: RequestReviewRequest) -> StateChangeReceipt:
        return self._write(req, "review-requests")

    def resolve_review(self, req: ResolveReviewRequest) -> StateChangeReceipt:
        return self._write(req, "review-resolutions")

    def _write(self, req, path: str) -> StateChangeReceipt:
        body = req.model_dump(mode="json", exclude={"run_id"})
        data = self._call("POST", f"/internal/v1/runs/{req.run_id}/{path}", json=body, run_id=req.run_id)
        return StateChangeReceipt.model_validate(data)

    def _call(self, method: str, url: str, *, json: Optional[dict] = None, run_id: Optional[str] = None) -> dict:
        try:
            resp = self._http.request(method, url, json=json, headers=self._headers)
        except httpx.TransportError as e:
            raise StatePersistenceError(f"Backend 호출 실패: {type(e).__name__}", run_id=run_id) from e
        if resp.status_code < 300:
            return resp.json()
        raise _map_error(resp, run_id)


def _map_error(resp: httpx.Response, run_id: Optional[str]) -> RunStateError:
    try:
        body = resp.json()
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}
    code, message, status = body.get("code", ""), body.get("message", ""), resp.status_code
    if status >= 500:
        return StatePersistenceError(message or f"Backend 오류 {status}", run_id=run_id)
    if code == "RUN_NOT_FOUND":
        return RunNotFound(message, run_id=run_id)
    if code == "RUN_RESULT_NOT_FOUND":
        return ResultNotFound(message, run_id=run_id)
    if code == "RUN_STATE_REVISION_CONFLICT":
        return StateRevisionConflict(message, run_id=run_id, expected_revision=body.get("expected_state_revision"),
                                     actual_revision=body.get("actual_state_revision"))
    if code == "RUN_OPERATION_CONFLICT":
        return OperationConflict(message, run_id=run_id)
    if code in ("RUN_INVALID_STATE_TRANSITION", "RUN_RESULT_ID_CONFLICT"):
        return InvalidStateTransition(message, run_id=run_id)
    if status in (401, 403):
        return RunAccessDenied(message or f"Backend 접근 거부 {status}", run_id=run_id)
    if status in (400, 413, 422):
        return RunRequestRejected(message or f"Backend 요청 거부 {status}", run_id=run_id)
    return RunStateError(message or f"Backend 응답 오류 {status} {code}", run_id=run_id)
