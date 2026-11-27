"""규칙 도구(#90)만으로 버전 케이스를 풀어 본 기준선 — LLM 없이 어디까지 되는지 확인용.

사용: python -m eval.doc_review.rule_baseline   → 버전 케이스(V*) 채점 결과
다중 문서 케이스는 LLM 판단이 필요해 여기서는 풀지 않는다(#91).
"""
import json

from app.tools.doc_review import version_diff
from eval.doc_review.dataset import load_cases, materialize
from eval.doc_review.score import score


def predict(case: dict) -> list[dict]:
    docs = materialize(case)
    diff = version_diff(docs)
    preds = []
    for item in diff["items"]:
        change = item["summary"]["change"]
        if change == "same":
            continue
        preds.append({"type": "CHANGE", "docs": [diff["document_id"]], "codes": [item["code"] or item["key"]],
                      "change": change, "minor": change == "minor", "value_change": item["summary"]["value_change"]})
    for u in diff["unchecked"]:
        preds.append({"type": "UNCHECKED", "docs": [diff["document_id"]], "codes": []})
    return preds


def run() -> dict:
    cases = [c for c in load_cases() if c["scope"] == "version"]
    predictions = {c["id"]: predict(c) for c in cases}
    return {"score": score(cases, predictions), "predictions": predictions}


if __name__ == "__main__":
    print(json.dumps(run()["score"], ensure_ascii=False, indent=1))
