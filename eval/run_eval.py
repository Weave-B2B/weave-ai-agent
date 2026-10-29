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
from app.tools.doc_tools import retrieval_label, search_docs, vector_backend  # noqa: E402

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


def agent2(splits=("validation",), pred_path: str | None = None) -> dict:
    """② LLM 에이전트 실행 평가 (bedrock·local이면 실제 호출 — 비용 발생). 정답은 비교에만 쓰고 에이전트 입력에는 넣지 않는다.
    목표: 라벨 정확도 ≥ 0.80, 근거 Hit ≥ 0.85, 인용 통과율 ≥ 0.95, 근거 없는 out_of_scope 0건."""
    from schema.schemas import Agent2Input
    from app.agents.registry import build_agent2
    from app.llm.client import build_llm

    agent = build_agent2(build_llm(settings))      # LLM_PROVIDER로 fake·local·bedrock, AGENT2_STYLE로 agent·workflow 선택
    n = correct = hit = hit_n = unsupported_oos = cited = verified = 0
    tokens, preds, confusion = 0, [], Counter()
    for s in splits:
        golds = {g["context"]["case_id"]: g for g in jl(EVAL / "agent2" / s / "gold.jsonl")}
        for inp in jl(EVAL / "agent2" / s / "input.jsonl"):
            out = agent.run(Agent2Input.model_validate(inp))
            preds.append(out.model_dump(mode="json"))
            q = out.meta.debug.get("quotes", {})
            cited, verified = cited + q.get("cited", 0), verified + q.get("verified", 0)
            tokens += out.meta.debug.get("input_tokens", 0) + out.meta.debug.get("output_tokens", 0)
            gold = {a["item_id"]: a for a in golds[inp["context"]["case_id"]]["assessments"]}
            for a in out.assessments:
                g = gold[a.item_id]
                n += 1
                correct += a.label == g["label"]
                confusion[(g["label"], a.label)] += 1
                unsupported_oos += a.label == "out_of_scope" and not a.evidence
                want = {(e["doc_id"], e["section_id"]) for e in g["evidence"] if e["role"] in ("basis", "superseding")}
                if g["label"] != "unclear" and want:
                    hit_n += 1
                    hit += bool(want & {(e.doc_id, e.section_id) for e in a.evidence})
    if pred_path:
        Path(pred_path).write_text("".join(json.dumps(p, ensure_ascii=False) + "\n" for p in preds), encoding="utf-8")
    return {"items": n, "label_acc": round(correct / n, 3) if n else None,
            "evidence_hit": round(hit / hit_n, 3) if hit_n else None,
            "quote_pass": round(verified / cited, 3) if cited else None, "unsupported_out_of_scope": unsupported_oos,
            "style": settings.agent2_style,
            "tokens": tokens, "confusion(gold→pred)": {f"{a}→{b}": c for (a, b), c in confusion.items() if a != b}}


def label_mix() -> dict:
    return dict(Counter(a["label"] for s in SPLITS for g in jl(EVAL / "agent2" / s / "gold.jsonl") for a in g["assessments"]))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("what", nargs="?", default="all", choices=["retrieval", "contract", "agent2", "all"])
    ap.add_argument("--split", choices=SPLITS)
    ap.add_argument("--pred", help="agent2: 예측 결과를 저장할 jsonl 경로")
    ap.add_argument("--retrieval", choices=["lexical", "vector", "hybrid"], help="검색 방식 (기본: RETRIEVAL_MODE)")
    a = ap.parse_args()
    if a.retrieval:
        settings.retrieval_mode = a.retrieval
        vector_backend.cache_clear()
    if a.what == "agent2":                              # API 비용이 드므로 'all'에 넣지 않는다. 기본은 validation
        print("agent2:", agent2((a.split or "validation",), a.pred))
        sys.exit(0)
    sp = (a.split,) if a.split else SPLITS
    if a.what in ("retrieval", "all"):
        print(f"retrieval({retrieval_label()}):", retrieval(sp))
    if a.what in ("contract", "all"):
        print("contract:", contract())
    print("gold label mix (review_needed):", label_mix())
