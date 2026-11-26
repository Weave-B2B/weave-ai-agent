"""Project-scoped PDF extraction using the R2 page gate and 2B cleanup."""
from __future__ import annotations

import base64
from contextlib import closing
import io
import logging
import re
from threading import Lock
from typing import Callable

import httpx

from app.kb.loader import chunk_uuid
from app.kb.vector_store import chunk_text

from .page_quality import judge_page

EXTRACTION_VERSION = "docling-luna-b1-v3"
log = logging.getLogger(__name__)
_pdf_lock = Lock()

DEFAULT_MODELS = {
    "bedrock": "global.anthropic.claude-haiku-4-5-20251001-v1:0",
    "openai": "gpt-6-luna",
}
# 이 오류는 잠시 뒤 다시 시도하면 될 수 있다. 권한·입력 오류는 다시 해도 같다
_BEDROCK_RETRYABLE = {"ThrottlingException", "ServiceUnavailableException", "ModelTimeoutException",
                      "InternalServerException", "ModelNotReadyException"}

Transcriber = Callable[[bytes, int], str]

_PROMPT = """이 PDF 페이지의 내용을 마크다운으로 정확히 옮겨줘.
- 글자는 보이는 그대로 옮기고, 요약하거나 고치지 마.
- 제목은 #, 표는 마크다운 표로 쓰고 병합 셀은 값을 반복해서 채워.
- 다단 구성이면 왼쪽 단을 먼저, 오른쪽 단을 나중에 써.
- 표의 칸 너비를 공백으로 맞추지 말고, 연속된 공백이나 빈 줄을 반복해서 넣지 마.
- 읽을 수 없는 글자는 [?]로 표시하고 추측하지 마.
- 설명 없이 결과만 출력해."""


class PageFallbackError(RuntimeError):
    def __init__(self, page: int, retryable: bool):
        super().__init__(f"페이지 {page}의 이미지 전사를 완료하지 못했습니다.")
        self.retryable = retryable


def _strip_fence(content: str) -> str:
    if content.startswith("```") and content.endswith("```") and content.count("```") == 2:
        return content.partition("\n")[2][:-3].strip()
    return content


def _transcribe(image: bytes, key: str, page: int, model: str = DEFAULT_MODELS["openai"]) -> str:
    """OpenAI로 페이지 이미지를 전사한다."""
    if not key:
        raise PageFallbackError(page, False)
    body = {
        "model": model,
        "max_completion_tokens": 4096,
        "temperature": 0,
        "reasoning_effort": "none",
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(image).decode()}},
            {"type": "text", "text": _PROMPT},
        ]}],
    }
    try:
        response = httpx.post("https://api.openai.com/v1/chat/completions",
                              headers={"Authorization": f"Bearer {key}"}, json=body, timeout=120)
        response.raise_for_status()
        choice = response.json()["choices"][0]
        content = (choice["message"]["content"] or "").strip()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        raise PageFallbackError(page, status == 429 or status >= 500) from None
    except (httpx.TransportError, ValueError, KeyError, IndexError, TypeError, AttributeError):
        raise PageFallbackError(page, True) from None
    content = _strip_fence(content)
    if choice.get("finish_reason") != "stop" or not content:
        raise PageFallbackError(page, False)
    return content


# 출력 한도에 걸리면 다시 시도하는 횟수. Haiku가 표 칸을 공백으로 맞추다 공백만 끝없이 내는 경우가 있다
# (SOW 스캔 2쪽: 내용 817자 + 공백 5.3만 자로 4096토큰 한도). 같은 입력도 다시 하면 대부분 정상 종료한다
_MAX_TOKENS_ATTEMPTS = 2


def _transcribe_bedrock(client, model: str, image: bytes, page: int) -> str:
    """Bedrock Converse로 페이지 이미지를 전사한다. 인증은 boto3 기본 자격 증명(배포는 Pod 역할)."""
    for attempt in range(_MAX_TOKENS_ATTEMPTS):
        content, stop = _converse_once(client, model, image, page)
        if stop == "end_turn" and content:
            return content
        if stop != "max_tokens":
            break
        log.info("전사 출력 한도 도달 page=%s attempt=%s — 다시 시도", page, attempt + 1)
    raise PageFallbackError(page, False)


def _converse_once(client, model: str, image: bytes, page: int) -> tuple[str, str]:
    from botocore.exceptions import BotoCoreError, ClientError

    try:
        response = client.converse(
            modelId=model,
            messages=[{"role": "user", "content": [
                {"image": {"format": "png", "source": {"bytes": image}}},
                {"text": _PROMPT},
            ]}],
            inferenceConfig={"maxTokens": 4096, "temperature": 0},
        )
        blocks = response["output"]["message"]["content"]
        content = "".join(block.get("text", "") for block in blocks).strip()
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        raise PageFallbackError(page, code in _BEDROCK_RETRYABLE) from None
    except BotoCoreError:
        raise PageFallbackError(page, True) from None
    except (KeyError, TypeError, AttributeError):
        raise PageFallbackError(page, False) from None
    return _strip_fence(content), response.get("stopReason")


def build_transcriber(provider: str, model: str = "", *, openai_key: str = "", region: str = "ap-northeast-2",
                      client=None) -> Transcriber:
    """설정(PDF_R2_TRANSCRIBE_PROVIDER·MODEL)으로 페이지 전사 함수를 만든다. 모델을 비우면 공급자 기본값."""
    model = model.strip() or DEFAULT_MODELS.get(provider, "")
    if provider == "openai":
        return lambda image, page: _transcribe(image, openai_key, page, model)
    if provider == "bedrock":
        if client is None:
            import boto3
            from botocore.config import Config

            client = boto3.client("bedrock-runtime", region_name=region,
                                  config=Config(read_timeout=120, connect_timeout=5, retries={"max_attempts": 2}))
        return lambda image, page: _transcribe_bedrock(client, model, image, page)
    raise ValueError(f"PDF_R2_TRANSCRIBE_PROVIDER는 bedrock·openai 중 하나: {provider}")


def _docling_pages(pdf: bytes):
    import pypdfium2 as pdfium
    from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.document import DocumentStream
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    # OCR은 끈다. 텍스트 층이 없거나 깨진 쪽은 judge_page가 골라 이미지 전사(Bedrock 등)로 대체하므로 중복이고,
    # 켜면 스캔본에서 메모리가 약 3배(1.6GB → 4.5GB)로 늘었다. 텍스트 PDF 결과는 같았다 (2026-11-26 측정)
    # 레이아웃·표 모델은 배포 이미지에 미리 받아 둔다 (DOCLING_ARTIFACTS_PATH, Dockerfile)
    options = PdfPipelineOptions(do_ocr=False, do_table_structure=True, generate_parsed_pages=True)
    options.accelerator_options = AcceleratorOptions(device=AcceleratorDevice.CPU, num_threads=4)
    converter = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)})
    result = converter.convert(DocumentStream(name="source.pdf", stream=io.BytesIO(pdf)))
    pages, source_lines, fallbacks = [], {}, []
    with pdfium.PdfDocument(pdf) as source:
        if len(source) != len(result.pages) or set(result.document.pages) != set(range(1, len(source) + 1)):
            raise ValueError("Docling 페이지 수 또는 번호가 원본과 다릅니다.")
        for number, parsed in enumerate(result.pages, 1):
            if parsed.page_no not in (number, number - 1):
                raise ValueError("Docling 페이지 순서가 원본과 다릅니다.")
            pages.append(result.document.export_to_markdown(page_no=number))
            with closing(source[number - 1]) as page:
                cells = [cell for cell in parsed.parsed_page.textline_cells if not cell.from_ocr] if parsed.parsed_page else []
                decision = judge_page(number, cells, page)
                if decision["reasons"]:
                    image = io.BytesIO()
                    with closing(page.render(scale=200 / 72)) as bitmap:
                        bitmap.to_pil().convert("RGB").save(image, format="PNG")
                    fallbacks.append((decision, image.getvalue()))
                else:
                    with closing(page.get_textpage()) as textpage:
                        source_lines[number] = [line.rstrip() for line in textpage.get_text_range().splitlines() if line.strip()]
    return pages, source_lines, fallbacks


def extract_pages(pdf: bytes, document_id: str, transcribe: Transcriber) -> tuple[list[str], list[dict]]:
    with _pdf_lock:
        pages, source_lines, fallbacks = _docling_pages(pdf)
    decisions = []
    for decision, image in fallbacks:
        number = decision["page"]
        log.info("전사 fallback document_version=%s page=%s reasons=%s", document_id, number, decision["reasons"])
        pages[number - 1] = transcribe(image, number)
        decisions.append(decision)
    from .parse_cleanup import clean_doc
    pages, _ = clean_doc(document_id, pages, source_lines)
    return pages, decisions


_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_SEP_CELL = re.compile(r"^\s*:?-{3,}:?\s*$")


def compact_tables(page: str) -> str:
    """docling 마크다운 표의 칸 채움 공백과 긴 구분선을 줄인다 (내용은 그대로).
    채움 문자가 청크 길이를 몇 배로 늘려 어휘 검색 점수를 떨어뜨리고 임베딩 입력도 낭비한다."""
    out = []
    for line in page.split("\n"):
        if _TABLE_ROW.match(line):
            cells = [c.strip() for c in line.strip()[1:-1].split("|")]
            if all(_TABLE_SEP_CELL.match(c) for c in cells):
                cells = ["---"] * len(cells)
            line = "| " + " | ".join(cells) + " |"
        out.append(line)
    return "\n".join(out)


def merge_wrapped_rows(page: str) -> str:
    """PDF 한 행이 줄바꿈으로 표 두 행이 된 경우(docling) 이어지는 행을 칸별로 앞 행에 합친다.
    '[A-F02][IN] … 상품 목록에' + 'A-F02 | (IN) | 서 상세 진입' → 문장 중간에 ID가 끼지 않는다.
    대상: 식별자('[A-F01]…:')가 있는 행 바로 뒤의, 식별자 없고 칸 수가 같은 행."""
    from .b1_units import row_key

    out: list[str] = []
    for line in page.split("\n"):
        prev = out[-1] if out else ""
        if (_TABLE_ROW.match(line) and _TABLE_ROW.match(prev) and row_key(prev) and not row_key(line)
                and not all(_TABLE_SEP_CELL.match(c) for c in _cells(line))):
            a, b = _cells(prev), _cells(line)
            if len(a) == len(b):
                out[-1] = "| " + " | ".join(" ".join(x for x in (p, q) if x) for p, q in zip(a, b)) + " |"
                continue
        out.append(line)
    return "\n".join(out)


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip()[1:-1].split("|")]


def build_chunks(document_id: str, pages: list[str]) -> tuple[list[str], list[dict]]:
    from .b1_units import ntok
    from .b1_v3 import build_view, chunk_document, metas_for

    pages = [merge_wrapped_rows(compact_tables(p)) for p in pages]
    meta = metas_for({document_id: pages})[document_id]
    chunks, _ = chunk_document(document_id, pages, meta)
    view = build_view(pages)
    stored = []
    for index, chunk in enumerate(chunks, 1):
        first, last = chunk["page_start"], chunk["page_end"]
        start, end = chunk["start"], chunk["end"]
        section = f"B1-{index:05d}"
        row = {
            "section_id": section,
            "extraction_version": EXTRACTION_VERSION,
            "chunk_id": chunk_uuid(document_id, section, EXTRACTION_VERSION),
            "heading": chunk["path"],
            "text": chunk["body"],
            "page": first,
            "page_end": last,
            "line_start": view["text"].count("\n", view["bounds"][first - 1][0], start) + 1,
            "line_end": view["text"].count("\n", view["bounds"][last - 1][0], end) + 1,
            "start_offset": start - (first - 1),
            "end_offset": end - (last - 1),
            "feature_id": None,
            "scope_flag": None,
        }
        if len(row["heading"]) > 500 or ntok(chunk_text(row)) > 500:
            raise ValueError("B1 경로 또는 최종 임베딩 문자열의 상한을 초과했습니다.")
        stored.append(row)
    return view["pages"], stored
