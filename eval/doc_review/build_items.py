"""문서 검토 평가셋(#89)의 항목 지도를 만든다 — 저장소 합성 문서(data/documents)를 새 파서(r2·B1-v3)로 읽은 결과.

사용: python -m eval.doc_review.build_items [문서ID ...]   (기본: A 세트 CTR·SOW·FSP·QUO·CO)
출력: eval/dataset/doc_review/items/<문서ID>.json — 문서 검토 에이전트가 받는 "항목 지도"와 같은 모양
      {document_id, document_type, version_no, items: [{item_id, code, heading, text, page}]}
스캔 쪽 전사는 쓰지 않는다(텍스트 PDF만). 파서가 바뀌면 다시 만들고 cases.jsonl의 item 코드가 그대로인지 확인한다.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.indexing import pdf_r2  # noqa: E402

OUT = ROOT / "eval" / "dataset" / "doc_review" / "items"
DEFAULT = ["CTR-A-001-v1.0", "SOW-A-001-v1.0", "FSP-A-001-v1.0", "QUO-A-001-v1.0", "CO-A-001-v1.0"]
TYPES = {"CTR": "CONTRACT", "SOW": "SOW", "FSP": "FUNCTIONAL_SPEC", "QUO": "QUOTATION", "CO": "CHANGE_ORDER"}
# 항목 코드: [A-F12] / TB-F10 / C-03 / 제3조 / Q-06 / CO-02 / REQ-001
CODE = re.compile(r"\[([A-Z]{1,5}-[A-Z]{0,3}\d{1,3}[A-Z]?)\]|(?<![A-Za-z0-9-])([A-Z]{1,5}-[A-Z]{0,3}\d{1,3}[A-Z]?)(?![A-Za-z0-9-])|(제\s*\d+\s*조)")


def item_code(heading: str, text: str) -> str | None:
    last = heading.split(">")[-1]
    for source in (last, text[:80]):
        m = CODE.search(source)
        if m:
            return re.sub(r"\s+", "", next(g for g in m.groups() if g))
    return None


def build(document_id: str) -> dict:
    pdf = (ROOT / "data" / "documents" / f"{document_id}.pdf").read_bytes()
    pages, _ = pdf_r2.extract_pages(pdf, document_id, lambda i, p: (_ for _ in ()).throw(RuntimeError("전사 필요")))
    _, chunks = pdf_r2.build_chunks(document_id, pages)
    items = [{"item_id": c["section_id"], "code": item_code(c["heading"], c["text"]), "heading": c["heading"],
              "text": c["text"], "page": c["page"]} for c in chunks]
    return {"document_id": document_id, "document_type": TYPES[document_id.split("-")[0]], "version_no": 1,
            "extraction_version": pdf_r2.EXTRACTION_VERSION, "items": items}


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for doc in sys.argv[1:] or DEFAULT:
        data = build(doc)
        (OUT / f"{doc}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        coded = sum(1 for i in data["items"] if i["code"])
        print(f"{doc}: 항목 {len(data['items'])}개 (코드 있음 {coded})")
