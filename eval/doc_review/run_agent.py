"""문서 검토 에이전트(#91)를 평가셋으로 돌리고 채점한다 (실제 LLM 호출 — 비용 발생).

사용: python -m eval.doc_review.run_agent [--cases V01,M03] [--out 결과.json]
LLM 설정은 .env(LLM_PROVIDER·LLM_MODEL 등)를 따른다. 결과 파일에 후보·트레이스·토큰 사용량을 남긴다.
"""
import argparse
import json
import time
from pathlib import Path

from app.agents.doc_review import DocReviewAgent, DocReviewInput
from app.core.config import settings
from app.llm.factory import build_llm
from eval.doc_review.dataset import load_cases, materialize
from eval.doc_review.score import score


def to_predictions(result) -> list[dict]:
    return [{"type": f.type, "docs": f.docs, "codes": f.codes, **({"change": f.change} if f.change else {}),
             "confidence": f.confidence, "title": f.title} for f in result.findings]


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default="")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    cases = load_cases()
    if a.cases:
        wanted = set(a.cases.split(","))
        cases = [c for c in cases if c["id"] in wanted]
    llm = build_llm(settings)
    predictions, runs = {}, {}
    for case in cases:
        started = time.time()
        result = DocReviewAgent(llm).run(DocReviewInput(as_of=case["as_of"], documents=materialize(case),
                                                        prior_judgments=case.get("prior_judgments", [])))
        predictions[case["id"]] = to_predictions(result)
        runs[case["id"]] = {"seconds": round(time.time() - started, 1), "usage": result.usage, "coverage": result.coverage,
                            "steps": len(result.trace), "findings": predictions[case["id"]], "trace": result.trace}
        print(f"{case['id']}: 후보 {len(predictions[case['id']])}개, 단계 {len(result.trace)}, "
              f"토큰 {result.usage['input_tokens']}/{result.usage['output_tokens']}, {runs[case['id']]['seconds']}s", flush=True)
    s = score(cases, predictions)
    print(json.dumps({k: v for k, v in s.items() if k != "cases"}, ensure_ascii=False))
    for row in s["cases"]:
        if row["missed"] or row["forbidden"] or row["extra"]:
            print(row)
    if a.out:
        Path(a.out).write_text(json.dumps({"score": s, "runs": runs}, ensure_ascii=False, indent=1), encoding="utf-8")
