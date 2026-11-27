"""문서 검토 평가셋(#89) 채점.

사용: python -m eval.doc_review.score <예측 JSON> [--cases eval/dataset/doc_review/cases.jsonl]
  예측 JSON: {케이스ID: [{"type", "docs": [문서ID...], "codes": [항목코드...], "minor"?: bool}, ...]}
  (문서 검토 에이전트 결과의 후보 목록을 이 모양으로 바꿔 넣는다)

맞춤 기준 — 기대 후보 하나에 대해 예측 후보가
  ① type이 같고 ② 기대 docs 중 하나 이상을 포함하고 ③ 기대 codes 중 하나 이상을 포함하면(기대 codes가 비면 생략) 맞힌 것으로 본다.
지표
  recall(required)     필수 기대 후보를 찾은 비율
  forbidden hits       음성 사례에서 보고하면 안 되는 후보를 보고한 수 (minor=true로 접은 CHANGE는 제외)
  extra                기대·선택 후보에 맞지 않는 나머지 예측 (오탐 후보 — 사람 검수 대상, 자동으로 오답 처리하지 않음). minor=true는 제외
  unchecked            UNCHECKED 필수 후보를 맞힌 비율 (빠짐없음 표시 확인)
"""
import argparse
import json
from pathlib import Path

from eval.doc_review.dataset import DATA, TYPES, load_cases


def matches(pred: dict, exp: dict) -> bool:
    if pred.get("type") != exp["type"]:
        return False
    if exp.get("docs") and not set(pred.get("docs", [])) & set(exp["docs"]):
        return False
    if exp.get("codes") and not set(pred.get("codes", [])) & set(exp["codes"]):
        return False
    return True


def score(cases: list[dict], predictions: dict[str, list[dict]]) -> dict:
    found = required = forbidden_hits = extra = 0
    unchecked_found = unchecked_total = 0
    rows = []
    for case in cases:
        preds = predictions.get(case["id"], [])
        bad = [p for p in preds if p.get("type") not in TYPES]
        if bad:
            raise ValueError(f"{case['id']}: 알 수 없는 type {[p.get('type') for p in bad]}")
        used = set()
        miss = []
        for exp in case.get("expected", []):
            hit = next((i for i, p in enumerate(preds) if i not in used and matches(p, exp)), None)
            if hit is not None:
                used.add(hit)
            if exp.get("required", True):
                required += 1
                found += hit is not None
                if hit is None:
                    miss.append(exp["type"])
                if exp["type"] == "UNCHECKED":
                    unchecked_total += 1
                    unchecked_found += hit is not None
        forb = 0
        for rule in case.get("forbidden", []):
            for i, p in enumerate(preds):
                if matches(p, rule) and not (rule.get("unless") == "minor" and p.get("minor")):
                    forb += 1
                    used.add(i)
        forbidden_hits += forb
        # 사소한 변경으로 접은 후보(minor=true)는 검수 대상 추가 후보로 세지 않는다
        case_extra = sum(1 for i, p in enumerate(preds) if i not in used and not p.get("minor"))
        extra += case_extra
        rows.append({"id": case["id"], "missed": miss, "forbidden": forb, "extra": case_extra})
    return {"recall_required": round(found / required, 3) if required else None, "required": required, "found": found,
            "forbidden_hits": forbidden_hits, "extra_for_review": extra,
            "unchecked_recall": round(unchecked_found / unchecked_total, 3) if unchecked_total else None, "cases": rows}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("predictions")
    ap.add_argument("--cases", default=str(DATA / "cases.jsonl"))
    a = ap.parse_args()
    result = score(load_cases(Path(a.cases)), json.loads(Path(a.predictions).read_text(encoding="utf-8")))
    print(json.dumps(result, ensure_ascii=False, indent=1))
