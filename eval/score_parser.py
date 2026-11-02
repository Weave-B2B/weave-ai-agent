"""PDF 파서 채점: data/documents/*.pdf → 조항 분할 결과를 data/kb/sections.jsonl(검수된 정답)과 비교한다.

    python eval/score_parser.py            # 요약
    python eval/score_parser.py --detail   # 틀린 조항 목록
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.kb.pdf_parser import parse_pdf  # noqa: E402

_WS = re.compile(r"\s+")


def score(detail: bool = False) -> dict:
    kb = ROOT / "data" / "kb"
    gold = [json.loads(l) for l in (kb / "sections.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    stored = json.loads((kb / "doc_pages.json").read_text(encoding="utf-8"))["docs"]
    by_doc: dict[str, dict] = {}
    for g in gold:
        by_doc.setdefault(g["doc_id"], {})[g["section_id"]] = g
    n = dict(gold=len(gold), found=0, extra=0, pages_identical=0, heading=0, position=0, text_exact=0, text_ws=0,
             feature=0, docs=len(by_doc))
    misses = []
    for doc, want in sorted(by_doc.items()):
        pages, secs = parse_pdf(ROOT / "data" / "documents" / f"{doc}.pdf")
        n["pages_identical"] += pages == stored.get(doc)
        got = {s.section_id: s for s in secs}
        n["extra"] += len(set(got) - set(want))
        for sid, g in want.items():
            s = got.get(sid)
            if s is None:
                misses.append((doc, sid, "missing"))
                continue
            n["found"] += 1
            checks = {
                "heading": s.heading == g["heading"],
                "position": (s.page, s.line_start, s.page_end, s.line_end) == (g["page"], g["line_start"], g["page_end"], g["line_end"]),
                "text_exact": s.text == g["text"],
                "text_ws": _WS.sub("", s.text) == _WS.sub("", g["text"]),
                "feature": (s.feature_id, s.scope_flag) == (g["feature_id"], g["scope_flag"]),
            }
            for k, ok in checks.items():
                n[k] += ok
            bad = [k for k, ok in checks.items() if not ok]
            if bad:
                misses.append((doc, sid, ",".join(bad), s, g))
    report = {"docs": n["docs"], "pages_identical": f"{n['pages_identical']}/{n['docs']}",
              "sections_found": f"{n['found']}/{n['gold']}", "extra_sections": n["extra"],
              **{k: f"{n[k]}/{n['gold']}" for k in ("heading", "position", "text_exact", "text_ws", "feature")}}
    if detail:
        for m in misses:
            print(m[0], m[1], m[2])
            if len(m) > 3:
                s, g = m[3], m[4]
                if "heading" in m[2]:
                    print("   heading got", repr(s.heading), "want", repr(g["heading"]))
                if "position" in m[2]:
                    print("   pos got", (s.page, s.line_start, s.page_end, s.line_end), "want", (g["page"], g["line_start"], g["page_end"], g["line_end"]))
                if "text" in m[2]:
                    print("   got ", repr(s.text[:220]))
                    print("   want", repr(g["text"][:220]))
    return report


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--detail", action="store_true")
    print(json.dumps(score(ap.parse_args().detail), ensure_ascii=False))
