"""문서 검토 평가셋(#89) 불러오기 — 케이스의 문서 버전을 항목 지도로 만든다.

케이스 문서는 기본 항목 지도(items/<문서ID>.json)에 ops를 적용한 버전이다.
  replace_text {code, find, with}   해당 코드 항목의 원문 일부를 바꾼다 (find가 없으면 오류)
  add_item     {after, item}         코드 after 항목 뒤에 새 항목을 넣는다
  remove_item  {code}                해당 코드 항목을 뺀다
extraction_failed=true면 항목 없이 "추출 실패" 문서로 준다 (미확인 판단 확인용).
"""
import copy
import json
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "dataset" / "doc_review"
TYPES = ("CHANGE", "SCOPE_MISMATCH", "MISSING", "VALUE_MISMATCH", "EFFECT_APPROVAL", "PRIOR_JUDGMENT_IMPACT", "UNCHECKED")


def load_cases(path: Path = DATA / "cases.jsonl") -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def base_items(document_id: str) -> dict:
    return json.loads((DATA / "items" / f"{document_id}.json").read_text(encoding="utf-8"))


def _find(items: list[dict], code: str) -> int:
    for index, item in enumerate(items):
        if item["code"] == code:
            return index
    raise KeyError(f"항목 코드 없음: {code}")


def apply_ops(doc: dict, ops: list[dict]) -> dict:
    doc = copy.deepcopy(doc)
    items = doc["items"]
    for op in ops:
        if op["op"] == "replace_text":
            item = items[_find(items, op["code"])]
            if op["find"] not in item["text"]:
                raise ValueError(f"{doc['document_id']} {op['code']}: 바꿀 문구를 찾지 못함 — {op['find'][:40]}")
            item["text"] = item["text"].replace(op["find"], op["with"], 1)
        elif op["op"] == "add_item":
            at = _find(items, op["after"]) + 1
            items.insert(at, {"item_id": f"ADD-{op['item']['code']}", "page": items[at - 1]["page"], **op["item"]})
        elif op["op"] == "remove_item":
            items.pop(_find(items, op["code"]))
        else:
            raise ValueError(f"알 수 없는 op: {op['op']}")
    return doc


def materialize(case: dict) -> list[dict]:
    """케이스의 문서 버전들을 항목 지도로 만든다. 같은 문서의 이전 ops는 이어받지 않는다(버전마다 v1 기준 ops)."""
    out = []
    for spec in case["documents"]:
        doc = base_items(spec["ref"])
        doc["version_no"] = spec.get("version_no", 1)
        if spec.get("extraction_failed"):
            doc["items"] = []
            doc["extraction_status"] = "FAILED"
        else:
            doc = apply_ops(doc, spec.get("ops", []))
            doc["extraction_status"] = "READY"
        out.append(doc)
    return out
