"""문서 도구: search_docs · get_section · verify_quote · list_baseline_docs (②가 소유, ①③도 사용)."""
import math
import re
from collections import Counter

from app.kb.loader import baselines, documents, sections
from app.tools.errors import ToolInputError
from app.tools.trace import traced


def _baseline(baseline_id: str) -> dict:
    b = baselines().get(baseline_id)
    if b is None:
        raise ToolInputError(f"unknown baseline_id {baseline_id}")
    return b


@traced("list_baseline_docs")
def list_baseline_docs(baseline_id: str) -> dict:
    b = _baseline(baseline_id)
    docs = [documents()[d] for d in b["document_ids"]]
    return {"baseline_id": baseline_id, **b, "documents": docs}


@traced("get_section")
def get_section(doc_id: str, section_id: str) -> dict:
    s = sections().get((doc_id, section_id))
    if s is None:
        raise ToolInputError(f"unknown section {doc_id}:{section_id}")
    return {"doc_id": doc_id, "doc_version": s["doc_version"], "section_id": section_id, "text": s["text"],
            "page": s["page"], "line_start": s["line_start"], "page_end": s["page_end"],
            "line_end": s["line_end"], "extraction_version": s["extraction_version"],
            # locator = ERD document_chunk_id 운반 ('chunk:<uuid>'). 다른 위치 참조와 구분하려 접두어를 붙인다
            "locator": s["locator"] or f"chunk:{s['chunk_id']}",
            "chunk_id": s["chunk_id"], "start_offset": s["start_offset"], "end_offset": s["end_offset"]}


_WS = re.compile(r"\s+")


@traced("verify_quote")
def verify_quote(quote: str, doc_id: str, section_id: str) -> dict:
    """quote가 섹션 원문의 부분 문자열인지 (공백 차이만 허용). 실패는 오류가 아니라 verified=False."""
    s = sections().get((doc_id, section_id))
    if s is None or not quote.strip():
        return {"verified": False, "matched_text": None}
    text = s["text"]
    chars = [(i, c) for i, c in enumerate(text) if not c.isspace()]
    flat = "".join(c for _, c in chars)
    q = _WS.sub("", quote)
    k = flat.find(q)
    if k < 0:
        return {"verified": False, "matched_text": None}
    start, end = chars[k][0], chars[k + len(q) - 1][0] + 1
    return {"verified": True, "matched_text": text[start:end]}


# ---- 검색 (MVP 기준선: 문자 bigram 어휘 검색. 하이브리드·RRF·리랭크로 교체 예정) ----
def _grams(t: str) -> Counter:
    t = _WS.sub("", t)
    return Counter(t[i:i + 2] for i in range(len(t) - 1))


@traced("search_docs", version="lexical-bigram-v0")
def search_docs(query: str, baseline_id: str, top_k: int = 5, filters: dict | None = None) -> list[dict]:
    """기준선의 승인 문서만 검색한다. 다른 프로젝트·기준선 밖 문서는 후보에도 오르지 않는다."""
    b = _baseline(baseline_id)
    allowed = set(b["document_ids"])
    if filters and filters.get("doc_types"):
        allowed = {d for d in allowed if documents()[d]["doc_type"] in filters["doc_types"]}
    pool = [s for (d, _), s in sections().items() if d in allowed]
    n = len(pool)
    grams = {id(s): _grams(s["heading"] + " " + s["text"]) for s in pool}
    df: Counter = Counter()
    for g in grams.values():
        df.update(g.keys())
    q = _grams(query)
    scored = []
    for s in pool:
        g = grams[id(s)]
        sc = sum(math.log(1 + n / df[t]) * min(c, g[t]) for t, c in q.items() if t in g)
        if sc > 0:
            scored.append((sc / math.sqrt(sum(g.values()) + 1), s))
    scored.sort(key=lambda x: -x[0])
    return [{"doc_id": s["doc_id"], "section_id": s["section_id"], "score": round(sc, 4), "text": s["text"],
             "page": s["page"], "line_start": s["line_start"], "line_end": s["line_end"]}
            for sc, s in scored[:top_k]]
