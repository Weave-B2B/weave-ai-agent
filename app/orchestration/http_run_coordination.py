"""Opt-in HTTP client for proposed Backend coordination endpoints.

Disabled by default: existing Backend installations do not implement this contract.
"""
from app.orchestration.http_run_state import HttpRunStateClient, RunRequestRejected
from app.orchestration.run_coordination import CoordinatedRun, ExecutionReceipt, FencedCommit


class HttpCoordinatedRunStateClient(HttpRunStateClient):
    def __init__(self, *args, coordination_enabled=False, **kwargs):
        super().__init__(*args, **kwargs)
        self._coordination_enabled = coordination_enabled

    def _enabled(self):
        if not self._coordination_enabled:
            raise RunRequestRejected("Backend coordination contract is not enabled")

    def get_coordinated_run(self, run_id):
        self._enabled()
        return CoordinatedRun.model_validate(self._call(
            "GET", f"/internal/v1/runs/{run_id}/coordination", run_id=run_id))

    def start_agent(self, req):
        raise RunRequestRejected("Use acquire_execution for coordinated runs")

    def request_review(self, req):
        raise RunRequestRejected("Use coordination questions or request_final_review")

    def resolve_review(self, req):
        raise RunRequestRejected("Use answer_question or final_review")

    def submit_input(self, req):
        raise RunRequestRejected("Use answer_question instead of legacy PMReview")

    def acquire_execution(self, req):
        self._enabled()
        body = req.model_dump(mode="json", exclude={"run_id"})
        return ExecutionReceipt.model_validate(self._call(
            "POST", f"/internal/v1/runs/{req.run_id}/executions/acquire", json=body, run_id=req.run_id))

    def commit_result(self, req):
        if not isinstance(req, FencedCommit):
            raise RunRequestRejected("Coordinated runs require a fenced result commit")
        return self._coordinated_write(req, "executions/commit")

    def finish_unchanged(self, req):
        return self._coordinated_write(req, "executions/unchanged")

    def save_coordination(self, req):
        return self._coordinated_write(req, "coordination")

    def answer_question(self, req):
        return self._coordinated_write(req, "question-answers")

    def request_final_review(self, req):
        return self._coordinated_write(req, "final-review-requests")

    def final_review(self, req):
        return self._coordinated_write(req, "final-review-decisions")

    def reserve_llm_attempt(self, req):
        return self._coordinated_write(req, "llm-attempts")

    def resume_run(self, req):
        return self._coordinated_write(req, "resume")

    def _coordinated_write(self, req, path):
        self._enabled()
        return self._write(req, path)
