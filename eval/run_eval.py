"""평가 실행기 (개발용). 사용: python -m eval.run_eval [retrieval|contract|all] [--split train|validation|test]

주의
- 모든 정답이 이미 노출돼 있어 test split을 독립 블라인드 성능으로 주장할 수 없다 (README '분할·검색').
- 정답은 검수 전(review_needed)이다. 아래 숫자는 개발 중 회귀 확인용이며 공식 성능 보고에 쓰지 않는다.
- A3 MD 정확도/MAPE는 검수 전 비활성이다 (md_accuracy_scoring=false).
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from app.kb.loader import documents  # noqa: E402
from app.orchestration.preflight import preflight_a3  # noqa: E402
from app.tools.doc_tools import search_docs  # noqa: E402

EVAL = settings.eval_dir
SPLITS = ("train", "validation", "test")


def jl(p) -> list[dict]:
    return [json.loads(l) for l in Path(p).read_text(encoding="utf-8").splitlines() if l.strip()]


def retrieval(splits=SPLITS, k: int = 5) -> dict:
    """② 검색 Hit@k·MRR. 정답 = 라벨이 unclear가 아닌 항목의 근거 (doc_id, section_id).
    검색어는 A1 정답 항목 text — 에이전트가 만드는 검색 계획이 아니라 검색기 자체의 기준선이다."""
    hit = n = 0
    rr = 0.0
    outside = 0
    for s in splits:
        ins = {r["context"]["case_id"]: r for r in jl(EVAL / "agent2" / s / "input.jsonl")}
        for g in jl(EVAL / "agent2" / s / "gold.jsonl"):
            ctx = g["context"]
            inp = ins[ctx["case_id"]]
            allowed = None
            for a in g["assessments"]:
                gold = {(e["doc_id"], e["section_id"]) for e in a["evidence"] if e["role"] in ("basis", "superseding")}
                if a["label"] == "unclear" or not gold:
                    continue
                item = next(i for i in inp["items"] if i["item_id"] == a["item_id"])
                res = search_docs(item["text"], ctx["baseline_id"], top_k=k)
                keys = [(r["doc_id"], r["section_id"]) for r in res]
                outside += sum(1 for r in res if documents()[r["doc_id"]]["project_id"] != ctx["project_id"])
                n += 1
                ranks = [keys.index(x) + 1 for x in gold if x in keys]
                if ranks:
                    hit += 1
                    rr += 1 / min(ranks)
    return {"items": n, f"hit@{k}": round(hit / n, 3) if n else None, "mrr": round(rr / n, 3) if n else None,
            "other_project_results": outside}


def contract() -> dict:
    cases = jl(EVAL / "agent3" / "contract_cases.jsonl")
    ok = 0
    for c in cases:
        gate = preflight_a3(c["input"])
        ok += (gate is None) if c["invoke_agent3"] else (gate is not None and gate.code == c["expected_gate"])
    return {"preflight_cases": len(cases), "match": ok}


def label_mix() -> dict:
    return dict(Counter(a["label"] for s in SPLITS for g in jl(EVAL / "agent2" / s / "gold.jsonl") for a in g["assessments"]))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("what", nargs="?", default="all", choices=["retrieval", "contract", "all"])
    ap.add_argument("--split", choices=SPLITS)
    a = ap.parse_args()
    sp = (a.split,) if a.split else SPLITS
    if a.what in ("retrieval", "all"):
        print("retrieval(lexical-bigram-v0):", retrieval(sp))
    if a.what in ("contract", "all"):
        print("contract:", contract())
    print("gold label mix (review_needed):", label_mix())
