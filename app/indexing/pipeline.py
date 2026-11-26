"""색인 1건 처리. 메시지 송수신(SQS)과 분리해 두어 SQS 수신부·개발용 실행이 같은 함수를 쓴다.

같은 요청을 다시 처리해도 결과가 같다 (VDB 설계 v0.3 1차 범위):
청크 ID는 UUID5, 벡터는 같은 ID·같은 내용이면 재사용, 결과 파일은 requestId별 고정 key에 같은 바이트로 덮어쓴다.
그래서 중단·재수신은 작업 전체를 다시 실행해 복구한다 (작업 장부·lease는 다중 worker 단계에서 추가).
"""
from __future__ import annotations

import hashlib
import json
import logging

from app.core.config import settings
from app.indexing import pdf_r2
from app.embed.base import Embedder
from app.indexing.messages import IndexRequest, IndexResult, ObjectRef
from app.indexing.objects import ObjectStore
from app.kb.pdf_parser import EXTRACTION_VERSION, PdfExtractError, PdfToolMissing, parse_pdf, to_chunks
from app.kb.vector_store import IndexConflict, PgVectorStore, build_pg_index, chunk_text, profile_id, text_hash

log = logging.getLogger(__name__)
MANIFEST_VERSION = "1.0"


class _Fail(Exception):
    def __init__(self, stage: str, code: str, retryable: bool, message: str):
        super().__init__(message)
        self.stage, self.code, self.retryable = stage, code, retryable


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def result_keys(request_id) -> tuple[str, str]:
    base = f"indexing/{request_id}"
    return f"{base}/snapshot.txt", f"{base}/manifest.json"


def manifest_bytes(m: dict) -> bytes:
    """UTF-8(BOM 없음)·LF·파일 끝 LF 한 개. checksum은 이 바이트 그대로의 SHA-256이다."""
    return (json.dumps(m, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def extraction_version_for(req: IndexRequest) -> str:
    """색인 요청에 쓸 추출 버전 (PDF_PARSER). 버전이 다르면 청크 ID도 달라 기존 색인과 섞이지 않는다."""
    return pdf_r2.EXTRACTION_VERSION if settings.pdf_parser == "r2" else EXTRACTION_VERSION


def _r2_pages(req, objects, result_bucket, pdf, source_sha):
    # Reuse the transcript for sequential retries of an immutable document version.
    # This also avoids paying for Luna again when embedding or reporting is retried.
    key = f"indexing/{pdf_r2.EXTRACTION_VERSION}/{req.project_id}/{req.document_version_id}/{source_sha}.json"
    try:
        cached = objects.get(result_bucket, key)
    except FileNotFoundError:
        cached = None
    except Exception as exc:
        # S3 reports a missing key as a ClientError, while permission and
        # transport failures must leave the request retryable.
        error = getattr(exc, "response", {}).get("Error", {}).get("Code")
        if error not in {"NoSuchKey", "404", "NotFound"}:
            raise _Fail("EXTRACT", "R2_CACHE_READ_FAILED", True, "파싱 결과를 읽지 못했습니다.") from exc
        cached = None
    if cached is not None:
        try:
            data = json.loads(cached)
            return data["pages"], data["pageFallbacks"]
        except (ValueError, KeyError, TypeError) as exc:
            raise _Fail("EXTRACT", "R2_CACHE_INVALID", False, "저장된 파싱 결과가 올바르지 않습니다.") from exc

    transcribe = pdf_r2.build_transcriber(settings.pdf_r2_transcribe_provider, settings.pdf_r2_transcribe_model,
                                          openai_key=settings.pdf_r2_openai_api_key, region=settings.aws_region)
    pages, decisions = pdf_r2.extract_pages(pdf, str(req.document_version_id), transcribe)
    try:
        objects.put(result_bucket, key, manifest_bytes({"pages": pages, "pageFallbacks": decisions}), "application/json")
    except Exception as exc:
        raise _Fail("EXTRACT", "R2_CACHE_WRITE_FAILED", True, "파싱 결과를 저장하지 못했습니다.") from exc
    return pages, decisions


def index_document(req: IndexRequest, objects: ObjectStore, embedder: Embedder, store: PgVectorStore,
                   source_bucket: str, result_bucket: str) -> IndexResult:
    profile = profile_id(embedder)
    version = extraction_version_for(req)
    base = dict(request_id=req.request_id, document_version_id=req.document_version_id,
                extraction_version=version, embedding_profile_id=profile)
    try:
        manifest = _run(req, objects, embedder, store, source_bucket, result_bucket, profile, version)
    except _Fail as f:
        log.warning("색인 실패 request=%s stage=%s code=%s", req.request_id, f.stage, f.code)
        return IndexResult(status="FAILED", failed_stage=f.stage, error_code=f.code, retryable=f.retryable,
                           error_message=str(f)[:500], **base)
    return IndexResult(status="READY", section_count=manifest[1], manifest=manifest[0], **base)


def _run(req, objects, embedder, store, source_bucket, result_bucket, profile, version) -> tuple[ObjectRef, int]:
    bucket = req.bucket or source_bucket
    if req.document_type == "IMPACT_WORKBOOK" or req.mime_type != "application/pdf":
        raise _Fail("EXTRACT", "UNSUPPORTED_MIME_TYPE", False, f"PDF만 지원: {req.mime_type}")
    try:
        pdf = objects.get(bucket, req.object_key)
    except Exception as exc:                             # 일시 장애일 수 있다
        raise _Fail("EXTRACT", "SOURCE_READ_FAILED", True, f"원본 읽기 실패: {type(exc).__name__}") from exc
    source_sha = _sha(pdf)
    if req.sha256 and req.sha256 != source_sha:
        raise _Fail("EXTRACT", "SOURCE_CHECKSUM_MISMATCH", False, "원본 SHA-256이 요청과 다름")
    dv, pj = str(req.document_version_id), str(req.project_id)
    decisions = []
    if version == pdf_r2.EXTRACTION_VERSION:
        try:
            pages, decisions = _r2_pages(req, objects, result_bucket, pdf, source_sha)
        except _Fail:
            raise
        except pdf_r2.PageFallbackError as exc:
            raise _Fail("EXTRACT", "LUNA_TRANSCRIPTION_FAILED", exc.retryable, str(exc)) from exc
        except ImportError as exc:
            raise _Fail("EXTRACT", "EXTRACT_TOOL_MISSING", False, type(exc).__name__) from exc
        except Exception as exc:
            raise _Fail("EXTRACT", "EXTRACT_FAILED", False, type(exc).__name__) from exc
        try:
            pages, chunks = pdf_r2.build_chunks(dv, pages)
        except ValueError as exc:
            raise _Fail("CHUNK", "CHUNK_INVALID", False, str(exc)) from exc
        if not chunks:
            raise _Fail("CHUNK", "NO_SECTIONS", False, "B1 청크가 없습니다.")
    else:
        try:
            pages, secs = parse_pdf(pdf)
        except PdfToolMissing as exc:
            raise _Fail("EXTRACT", "EXTRACT_TOOL_MISSING", True, str(exc)) from exc
        except PdfExtractError as exc:
            raise _Fail("EXTRACT", "EXTRACT_FAILED", False, str(exc)) from exc
        if not secs:
            raise _Fail("CHUNK", "NO_SECTIONS", False, "조항을 찾지 못함 — 지원하지 않는 양식일 수 있음")
        try:
            chunks = to_chunks(dv, pages, secs)
        except ValueError as exc:
            raise _Fail("CHUNK", "DUPLICATE_SECTION_ID", False, str(exc)) from exc
    try:
        build_pg_index(embedder, {(dv, c["section_id"]): c for c in chunks}, store, lambda _: (dv, pj))
    except IndexConflict as exc:
        raise _Fail("EMBED", "INDEX_CONFLICT", False, str(exc)) from exc
    except Exception as exc:                             # 모델·DB 일시 장애
        raise _Fail("EMBED", "EMBED_FAILED", True, f"임베딩·벡터 저장 실패: {type(exc).__name__}") from exc

    snapshot = "\f".join(pages).encode("utf-8")
    snap_key, man_key = result_keys(req.request_id)
    manifest = {
        "schemaVersion": MANIFEST_VERSION, "requestId": str(req.request_id), "projectId": pj,
        "documentVersionId": dv, "extractionVersion": version, "embeddingProfileId": profile,
        "sourceSha256": source_sha,
        "textSnapshot": {"bucket": result_bucket, "key": snap_key, "sha256": _sha(snapshot)},
        "chunkCount": len(chunks),
        "chunks": [{"chunkId": c["chunk_id"], "sectionId": c["section_id"], "heading": c["heading"], "text": c["text"],
                    "pageStart": c["page"], "pageEnd": c["page_end"], "lineStart": c["line_start"], "lineEnd": c["line_end"],
                    "startOffset": c["start_offset"], "endOffset": c["end_offset"],
                    "featureId": c["feature_id"], "scopeFlag": c["scope_flag"],
                    "textSha256": _sha(c["text"].encode("utf-8")), "embeddingInputSha256": text_hash(chunk_text(c))}
                   for c in chunks],
    }
    if version == pdf_r2.EXTRACTION_VERSION:
        manifest["pageFallbacks"] = decisions
    body = manifest_bytes(manifest)
    try:
        objects.put(result_bucket, snap_key, snapshot, "text/plain; charset=utf-8")
        objects.put(result_bucket, man_key, body, "application/json")
    except Exception as exc:
        # 백엔드 failedStage에 업로드 단계가 없어 마지막 단계(EMBED)로 보낸다 — CN과 값 추가 협의
        raise _Fail("EMBED", "RESULT_UPLOAD_FAILED", True, f"결과 파일 업로드 실패: {type(exc).__name__}") from exc
    return ObjectRef(bucket=result_bucket, key=man_key, sha256=_sha(body)), len(chunks)
