"""요청 큐(SQS) 수신 → 기존 실행 함수 호출 → 결과는 실행 함수가 결과 큐로 보낸다.

처리 규칙
- 메시지를 받으면 처리하는 동안 Visibility Timeout을 계속 연장한다 (heartbeat). 처리 시간이 길어도 다른 워커가 가져가지 않는다.
- 결과 발행까지 끝나면 요청 메시지를 지운다. 처리 중 죽거나 발행에 실패하면 지우지 않아 큐가 다시 보낸다.
- 마지막 수신(최대 수신 횟수)에서도 실패하면 실패 결과를 보내고 지운다 — DLQ로 넘어가 "처리 중"으로 멈추지 않게.
  실패 결과까지 못 보내면 DLQ로 넘어간다 (CN DLQ 모니터링 대상).
- 다시 보내도 처리할 수 없는 메시지(형식 오류)는 실패 결과를 보낼 수 있으면 보내고 지운다.
- Standard 큐는 같은 메시지가 두 번 올 수 있다. 이 워커가 이미 끝낸 요청은 다시 실행하지 않는다 (프로세스 안에서만).
"""
from __future__ import annotations

import json
import logging
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable, Optional

from pydantic import ValidationError

log = logging.getLogger(__name__)

RECEIVE_WAIT_SEC = 20          # long polling
RECEIVE_ERROR_BACKOFF_SEC = 5
RETRY_VISIBILITY_SEC = 30      # 실패한 메시지를 다시 보여 주기까지 (마지막 수신 전)


class Unprocessable(ValueError):
    """다시 받아도 처리할 수 없는 메시지"""


@dataclass(frozen=True)
class Job:
    """메시지 하나를 처리하는 방법."""
    key: str                                   # 중복 확인용 (분석 ID, 색인 요청 ID)
    run: Callable[[], None]                    # 실행 + 결과 발행 (발행 실패는 예외)
    give_up: Callable[[], None]                # 마지막 시도 실패 시 실패 결과 발행


Parser = Callable[[dict], Job]                  # 본문 → Job. 형식 오류는 Unprocessable


class Recent:
    """최근 처리한 요청 키 (중복 메시지 건너뛰기용)."""

    def __init__(self, size: int = 1000):
        self._keys: OrderedDict[str, None] = OrderedDict()
        self._size = size
        self._lock = threading.Lock()

    def __contains__(self, key: str) -> bool:
        with self._lock:
            return key in self._keys

    def add(self, key: str) -> None:
        with self._lock:
            self._keys[key] = None
            self._keys.move_to_end(key)
            while len(self._keys) > self._size:
                self._keys.popitem(last=False)


class Worker:
    def __init__(self, client: Any, queues: dict[str, Parser], *, concurrency: int, visibility_sec: int,
                 max_receive_count: int, recent: Optional[Recent] = None):
        self.client = client
        self.queues = queues
        self.visibility_sec = visibility_sec
        self.max_receive_count = max_receive_count
        self.recent = recent or Recent()
        self.stop = threading.Event()
        self._slots = threading.Semaphore(concurrency)
        self._pool = ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="job")

    # ---- 수신 ----
    def run_forever(self) -> None:
        pollers = [threading.Thread(target=self._poll, args=(url,), name=f"poll-{i}", daemon=True)
                   for i, url in enumerate(self.queues)]
        for t in pollers:
            t.start()
        log.info("워커 시작: 큐 %d개", len(pollers))
        for t in pollers:
            t.join()
        self._pool.shutdown(wait=True)            # 처리 중인 메시지는 끝까지 처리한다
        log.info("워커 종료")

    def _poll(self, queue_url: str) -> None:
        while not self.stop.is_set():
            self._slots.acquire()
            if self.stop.is_set():                    # 자리를 기다리는 동안 종료 신호가 왔다
                self._slots.release()
                break
            try:
                messages = self._receive(queue_url)
            except Exception:
                self._slots.release()
                log.exception("메시지 수신 실패 queue=%s", queue_url)
                self.stop.wait(RECEIVE_ERROR_BACKOFF_SEC)
                continue
            if not messages:
                self._slots.release()
                continue
            self._pool.submit(self._handle_and_release, queue_url, messages[0])

    def _receive(self, queue_url: str) -> list[dict]:
        resp = self.client.receive_message(
            QueueUrl=queue_url, MaxNumberOfMessages=1, WaitTimeSeconds=RECEIVE_WAIT_SEC,
            VisibilityTimeout=self.visibility_sec, AttributeNames=["ApproximateReceiveCount"])
        return resp.get("Messages", [])

    def _handle_and_release(self, queue_url: str, message: dict) -> None:
        try:
            self.handle(queue_url, message)
        finally:
            self._slots.release()

    # ---- 처리 ----
    def handle(self, queue_url: str, message: dict) -> None:
        receipt = message["ReceiptHandle"]
        receive_count = int(message.get("Attributes", {}).get("ApproximateReceiveCount", "1"))
        try:
            job = self.queues[queue_url](json.loads(message["Body"]))
        except (Unprocessable, ValueError, ValidationError) as e:
            log.error("처리할 수 없는 메시지라 지움 queue=%s: %s", queue_url, e)
            self._delete(queue_url, receipt)
            return
        if job.key in self.recent:
            log.info("이미 처리한 요청이라 건너뜀 key=%s", job.key)
            self._delete(queue_url, receipt)
            return

        heartbeat = _Heartbeat(self.client, queue_url, receipt, self.visibility_sec)
        heartbeat.start()
        try:
            job.run()
        except Exception:
            log.exception("메시지 처리 실패 key=%s receive=%d/%d", job.key, receive_count, self.max_receive_count)
            heartbeat.stop()
            self._after_failure(queue_url, receipt, job, receive_count)
            return
        finally:
            heartbeat.stop()
        self.recent.add(job.key)
        self._delete(queue_url, receipt)

    def _after_failure(self, queue_url: str, receipt: str, job: Job, receive_count: int) -> None:
        if receive_count < self.max_receive_count:          # 조금 뒤 다시 받는다
            self._change_visibility(queue_url, receipt, RETRY_VISIBILITY_SEC)
            return
        try:                                                 # 마지막 시도 — 실패를 알리고 끝낸다
            job.give_up()
        except Exception:
            log.exception("실패 결과도 보내지 못함 — DLQ로 넘어감 key=%s", job.key)
            return
        self.recent.add(job.key)
        self._delete(queue_url, receipt)

    def _delete(self, queue_url: str, receipt: str) -> None:
        try:
            self.client.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt)
        except Exception:                                    # 지우지 못하면 다시 오지만 중복 확인으로 건너뛴다
            log.exception("메시지 삭제 실패 queue=%s", queue_url)

    def _change_visibility(self, queue_url: str, receipt: str, seconds: int) -> None:
        try:
            self.client.change_message_visibility(QueueUrl=queue_url, ReceiptHandle=receipt, VisibilityTimeout=seconds)
        except Exception:
            log.exception("Visibility 변경 실패 queue=%s", queue_url)


class _Heartbeat:
    """처리하는 동안 visibility_sec의 절반마다 다시 visibility_sec으로 연장한다."""

    def __init__(self, client: Any, queue_url: str, receipt: str, visibility_sec: int):
        self._args = dict(QueueUrl=queue_url, ReceiptHandle=receipt, VisibilityTimeout=visibility_sec)
        self._interval = visibility_sec / 2
        self._client = client
        self._done = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="heartbeat", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._done.set()

    def _loop(self) -> None:
        while not self._done.wait(self._interval):
            try:
                self._client.change_message_visibility(**self._args)
            except Exception:
                log.exception("Visibility 연장 실패 queue=%s", self._args["QueueUrl"])
