"""Execute one chosen tool against the proposed authoritative Run port.

This is an execution adapter, not a new selector or a second state store.
Persistence retries never rerun the supplied worker/model call.
"""
from uuid import uuid4

from app.orchestration.payload import to_commit_payload
from app.orchestration.run_coordination import (
    AcquireExecution, CoordinatedRunClient, FencedCommit, FinishUnchanged, payload_fingerprint,
)
from app.orchestration.run_state import StatePersistenceError, StateRevisionConflict


class CoordinatedAgentRunner:
    def __init__(self, state: CoordinatedRunClient):
        self.state = state

    def _write(self, method, request):
        for attempt in range(3):
            try:
                return method(request)
            except StatePersistenceError:
                if attempt == 2:
                    raise

    def execute(self, run_id, agent_type, worker, *, expected_revision=None):
        """Caller selects the tool and builds inputs from authoritative results.

        Backend capability opt-in is required for HTTP. No calls are made through
        the legacy PMReview path and no approval is fabricated here.
        """
        initial = self.state.get_state(run_id)
        if expected_revision is not None and initial.state_revision != expected_revision:
            raise StateRevisionConflict("Run changed after tool selection", run_id=run_id,
                expected_revision=expected_revision, actual_revision=initial.state_revision)
        acquired = self._write(self.state.acquire_execution, AcquireExecution(
            run_id=run_id, agent_type=agent_type, expected_state_revision=initial.state_revision,
            operation_id=uuid4().hex))
        owned = self.state.get_coordinated_run(run_id)
        lease = owned.executions[agent_type]
        result_id = uuid4().hex
        try:
            output = worker()
            if agent_type in ("agent1", "agent2"):
                output = output.model_copy(deep=True)
                output.meta.result_id = result_id
            payload = to_commit_payload(output)
        except Exception:
            current = self.state.get_state(run_id)
            self._write(self.state.commit_result, FencedCommit(
                run_id=run_id, agent_type=agent_type, expected_state_revision=current.state_revision,
                operation_id=uuid4().hex, execution_token=acquired.execution_token,
                execution_status="FAILED", requested_run_status="FAILED", result_id=uuid4().hex,
                upstream_result_ids=lease.upstream_result_ids,
                result_payload={"error": {"code": "TOOL_EXECUTION_FAILED", "stage": agent_type}}))
            raise
        current = self.state.get_state(run_id)
        previous = initial.agents[agent_type]
        if previous.current_result_id and not previous.stale:
            prior = self.state.get_result(run_id, previous.current_result_id)
            if (prior.upstream_result_ids == lease.upstream_result_ids
                    and payload_fingerprint(prior.result_payload) == payload_fingerprint(payload)):
                return self._write(self.state.finish_unchanged, FinishUnchanged(
                    run_id=run_id, agent_type=agent_type, expected_state_revision=current.state_revision,
                    operation_id=uuid4().hex, execution_token=acquired.execution_token,
                    target_result_id=prior.result_id, observed_payload=payload))
        return self._write(self.state.commit_result, FencedCommit(
            run_id=run_id, agent_type=agent_type, expected_state_revision=current.state_revision,
            operation_id=uuid4().hex, execution_token=acquired.execution_token,
            result_id=result_id, upstream_result_ids=lease.upstream_result_ids, result_payload=payload))
