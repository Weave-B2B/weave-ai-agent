"""SQS 워커: 메시지 처리·삭제·재시도·마지막 시도 실패·중복·Visibility 연장, 결과 큐 발행 형식."""
import json
import threading
import time
from uuid import uuid4

import pytest

from app.api import agent1_routes, internal_analysis_routes, internal_ingest_routes
from app.core.config import settings
from app.indexing.messages import IndexResult
from app.messaging import sqs
from app.worker import jobs
from app.worker.__main__ import queues_from
from app.worker.runner import Job, Unprocessable, Worker, RETRY_VISIBILITY_SEC

Q = "http://sqs/weave-dev-analysis-request"


class FakeSqs:
    def __init__(self):
        self.sent, self.deleted, self.visibility = [], [], []
        self.lock = threading.Lock()

    def send_message(self, QueueUrl, MessageBody):
        self.sent.append((QueueUrl, json.loads(MessageBody)))

    def delete_message(self, QueueUrl, ReceiptHandle):
        self.deleted.append(ReceiptHandle)

    def change_message_visibility(self, QueueUrl, ReceiptHandle, VisibilityTimeout):
        with self.lock:
            self.visibility.append((ReceiptHandle, VisibilityTimeout))


def _msg(body, receive_count=1, receipt="r1"):
    return {"ReceiptHandle": receipt, "Body": json.dumps(body), "Attributes": {"ApproximateReceiveCount": str(receive_count)}}


def _worker(client, parser, visibility_sec=60):
    return Worker(client, {Q: parser}, concurrency=1, visibility_sec=visibility_sec, max_receive_count=3)


def _job(calls, *, fail=False, give_up_fails=False, key="k1", delay=0.0):
    def run():
        time.sleep(delay)
        calls.append("run")
        if fail:
            raise RuntimeError("publish failed")

    def give_up():
        calls.append("give_up")
        if give_up_fails:
            raise RuntimeError("still failing")

    return lambda _body: Job(key=key, run=run, give_up=give_up)


def test_success_deletes_and_skips_duplicate():
    client, calls = FakeSqs(), []
    w = _worker(client, _job(calls))
    w.handle(Q, _msg({}, receipt="r1"))
    w.handle(Q, _msg({}, receipt="r2"))                     # Standard 큐 중복 전달
    assert calls == ["run"] and client.deleted == ["r1", "r2"]


def test_failure_before_last_attempt_is_retried_later():
    client, calls = FakeSqs(), []
    _worker(client, _job(calls, fail=True)).handle(Q, _msg({}, receive_count=1))
    assert calls == ["run"] and client.deleted == []
    assert ("r1", RETRY_VISIBILITY_SEC) in client.visibility


def test_last_attempt_failure_reports_and_deletes():
    client, calls = FakeSqs(), []
    _worker(client, _job(calls, fail=True)).handle(Q, _msg({}, receive_count=3))
    assert calls == ["run", "give_up"] and client.deleted == ["r1"]


def test_give_up_failure_leaves_message_for_dlq():
    client, calls = FakeSqs(), []
    _worker(client, _job(calls, fail=True, give_up_fails=True)).handle(Q, _msg({}, receive_count=3))
    assert client.deleted == []


def test_unprocessable_message_is_deleted():
    client = FakeSqs()

    def parser(_body):
        raise Unprocessable("bad")

    _worker(client, parser).handle(Q, _msg({}))
    assert client.deleted == ["r1"]


def test_heartbeat_extends_visibility_while_running():
    client, calls = FakeSqs(), []
    _worker(client, _job(calls, delay=0.35), visibility_sec=0.2).handle(Q, _msg({}))
    assert ("r1", 0.2) in client.visibility and client.deleted == ["r1"]


# ---- 결과 큐 발행 형식 ----
@pytest.fixture
def sqs_mode(monkeypatch):
    client = FakeSqs()
    monkeypatch.setattr(settings, "result_transport", "sqs")
    monkeypatch.setattr(settings, "sqs_analysis_result_url", "http://sqs/analysis-result")
    monkeypatch.setattr(settings, "sqs_index_result_url", "http://sqs/index-result")
    monkeypatch.setattr(sqs, "shared_client", lambda _s: client)
    return client


def test_analysis_result_messages(sqs_mode):
    ai_id = uuid4()
    internal_analysis_routes._report(ai_id, {"status": "READY"}, None)
    internal_analysis_routes._report(ai_id, None, ("MODEL_TIMEOUT", True))
    assert sqs_mode.sent == [
        ("http://sqs/analysis-result", {"aiAnalysisId": str(ai_id), "outcome": "COMPLETED", "output": {"status": "READY"}}),
        ("http://sqs/analysis-result", {"aiAnalysisId": str(ai_id), "outcome": "FAILED", "errorCode": "MODEL_TIMEOUT",
                                        "retryable": True}),
    ]


def test_too_large_result_is_reported_as_failure(sqs_mode, monkeypatch):
    monkeypatch.setattr(sqs, "MAX_MESSAGE_BYTES", 200)           # 실패 메시지(약 120B)는 들어가고 결과는 넘친다
    internal_analysis_routes._report(uuid4(), {"text": "x" * 500}, None)
    (_, body), = sqs_mode.sent
    assert body["outcome"] == "FAILED" and body["errorCode"] == "OUTPUT_INVALID" and body["retryable"] is False


def test_index_result_message_is_a_mq_02(sqs_mode):
    dv = uuid4()
    result = IndexResult(status="FAILED", request_id=uuid4(), document_version_id=dv, failed_stage="CHUNK",
                         error_code="NO_SECTIONS", retryable=False)
    internal_ingest_routes._report(dv, result)
    assert sqs_mode.sent == [("http://sqs/index-result", result.to_json())]


# ---- 요청 메시지 → 실행 ----
def test_analysis_job_runs_agent1_and_publishes(sqs_mode, monkeypatch):
    monkeypatch.setattr(settings, "agent_mode", "fixture")
    agent1_routes.get_agent1.cache_clear()
    ai_id = uuid4()
    body = {"aiAnalysisId": str(ai_id), "analysisType": "DECOMPOSITION", "input": {
        "context": {"project_id": "P", "case_id": "CR-A-001", "request_revision": 1},
        "received_at": "2026-11-20T10:00:00+09:00", "sender_role": "고객", "message_text": "소셜 로그인 추가"}}
    job = jobs.analysis_job(body)
    assert job.key == f"analysis:{ai_id}"
    job.run()
    agent1_routes.get_agent1.cache_clear()
    (_, msg), = sqs_mode.sent
    assert msg["outcome"] == "COMPLETED" and msg["output"]["items"]


def test_analysis_job_with_invalid_input_reports_invalid(sqs_mode):
    ai_id = uuid4()
    jobs.analysis_job({"aiAnalysisId": str(ai_id), "analysisType": "SCOPE", "input": {"context": {}}}).run()
    (_, msg), = sqs_mode.sent
    assert msg == {"aiAnalysisId": str(ai_id), "outcome": "FAILED", "errorCode": "INVALID_INPUT", "retryable": False}


def test_malformed_requests_are_unprocessable():
    with pytest.raises(Unprocessable):
        jobs.analysis_job({"analysisType": "SCOPE"})
    with pytest.raises(Unprocessable):
        jobs.index_job({"documentVersionId": "x"})


def test_worker_queue_selection(monkeypatch):
    monkeypatch.setattr(settings, "sqs_index_request_url", "http://sqs/index-request")
    monkeypatch.setattr(settings, "sqs_analysis_request_url", "http://sqs/analysis-request")
    monkeypatch.setattr(settings, "worker_queues", "analysis")
    assert list(queues_from(settings)) == ["http://sqs/analysis-request"]
    monkeypatch.setattr(settings, "worker_queues", "index,analysis")
    assert len(queues_from(settings)) == 2
    monkeypatch.setattr(settings, "worker_queues", "agent1")
    with pytest.raises(ValueError):
        queues_from(settings)
