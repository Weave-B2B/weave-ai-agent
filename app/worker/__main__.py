"""AI 워커 실행: python -m app.worker

설정 (.env 또는 환경 변수)
  RESULT_TRANSPORT=sqs                결과를 결과 큐로 보낸다 (http면 백엔드 내부 API로 보낸다)
  SQS_INDEX_REQUEST_URL / SQS_ANALYSIS_REQUEST_URL   받을 요청 큐
  SQS_INDEX_RESULT_URL / SQS_ANALYSIS_RESULT_URL     보낼 결과 큐
  WORKER_QUEUES=index,analysis        받을 큐 (색인·분석 워커를 나눠 배포할 때 하나만)
  WORKER_CONCURRENCY=2                동시에 처리할 메시지 수
  SQS_ENDPOINT=http://localhost:4566  로컬 LocalStack. 배포는 비운다 (Pod IAM 역할)
SIGTERM(배포 교체)을 받으면 새 메시지를 받지 않고, 처리 중인 메시지는 끝낸 뒤 종료한다.
"""
from __future__ import annotations

import logging
import signal
import sys

from app.core.config import settings
from app.messaging.sqs import build_sqs_client
from app.worker import jobs
from app.worker.runner import Worker

log = logging.getLogger("app.worker")


def queues_from(cfg) -> dict:
    wanted = {q.strip() for q in cfg.worker_queues.split(",") if q.strip()}
    unknown = wanted - {"index", "analysis"}
    if unknown or not wanted:
        raise ValueError(f"WORKER_QUEUES는 index·analysis 중에서 고른다: {cfg.worker_queues}")
    queues = {}
    if "index" in wanted:
        queues[_required(cfg.sqs_index_request_url, "SQS_INDEX_REQUEST_URL")] = jobs.index_job
    if "analysis" in wanted:
        queues[_required(cfg.sqs_analysis_request_url, "SQS_ANALYSIS_REQUEST_URL")] = jobs.analysis_job
    return queues


def _required(value: str, name: str) -> str:
    if not value:
        raise ValueError(f"{name}가 비어 있습니다.")
    return value


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        queues = queues_from(settings)
    except ValueError as e:
        log.error("워커 설정 오류: %s", e)
        return 2
    if settings.result_transport != "sqs":
        log.warning("RESULT_TRANSPORT=%s — 결과를 결과 큐가 아니라 백엔드 내부 API로 보낸다", settings.result_transport)
    worker = Worker(build_sqs_client(settings), queues, concurrency=settings.worker_concurrency,
                    visibility_sec=settings.worker_visibility_sec, max_receive_count=settings.worker_max_receive_count)

    def shutdown(signum, _frame):
        log.info("종료 신호(%s) — 새 메시지를 받지 않고 처리 중인 메시지를 끝낸다", signal.Signals(signum).name)
        worker.stop.set()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    worker.run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
