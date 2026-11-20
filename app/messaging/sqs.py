"""SQS 클라이언트와 결과 메시지 발행.

결과 큐 메시지 본문은 HTTP 결과 전달과 같은 내용을 JSON으로 담는다.
- 색인 결과 (weave-dev-index-result): A-MQ-02 (IndexResult.to_json(), HTTP ingest-result 본문과 같다)
- 분석 결과 (weave-dev-analysis-result):
    {"aiAnalysisId": ..., "outcome": "COMPLETED", "output": {...}}            HTTP complete 본문 = output
    {"aiAnalysisId": ..., "outcome": "FAILED", "errorCode": ..., "retryable": ...}  HTTP fail 본문과 같다
발행이 실패하면 예외를 그대로 던진다. 워커는 요청 메시지를 지우지 않아 큐가 다시 처리하게 한다.
"""
from __future__ import annotations

import json
from typing import Any, Optional
from uuid import UUID

MAX_MESSAGE_BYTES = 256 * 1024 - 1024     # SQS 한도 256KB, 메시지 속성 여유


class MessageTooLarge(ValueError):
    pass


def build_sqs_client(settings) -> Any:
    """LocalStack(SQS_ENDPOINT)이면 S3와 같은 개발용 키를 쓰고, 배포는 boto3 기본 자격 증명(Pod IAM 역할)을 쓴다."""
    import boto3
    keys = {}
    if settings.sqs_endpoint and settings.s3_access_key_id and settings.s3_secret_access_key:
        keys = {"aws_access_key_id": settings.s3_access_key_id, "aws_secret_access_key": settings.s3_secret_access_key}
    return boto3.client("sqs", endpoint_url=settings.sqs_endpoint or None, region_name=settings.sqs_region, **keys)


_shared: Any = None


def shared_client(settings) -> Any:
    """프로세스에서 하나만 만든다 (boto3 클라이언트는 스레드끼리 같이 써도 된다)."""
    global _shared
    if _shared is None:
        _shared = build_sqs_client(settings)
    return _shared


def encode(body: dict) -> str:
    data = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    if len(data.encode("utf-8")) > MAX_MESSAGE_BYTES:
        raise MessageTooLarge(f"메시지가 SQS 한도를 넘음: {len(data.encode('utf-8'))} bytes")
    return data


def publish(client: Any, queue_url: str, body: dict) -> None:
    if not queue_url:
        raise ValueError("결과 큐 URL이 비어 있습니다.")
    client.send_message(QueueUrl=queue_url, MessageBody=encode(body))


def analysis_result(ai_analysis_id: UUID, output: Optional[dict], failure: Optional[tuple[str, bool]]) -> dict:
    if failure:
        code, retryable = failure
        return {"aiAnalysisId": str(ai_analysis_id), "outcome": "FAILED", "errorCode": code, "retryable": retryable}
    return {"aiAnalysisId": str(ai_analysis_id), "outcome": "COMPLETED", "output": output}
