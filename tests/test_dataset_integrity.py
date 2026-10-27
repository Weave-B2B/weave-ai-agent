"""데이터셋·스키마 무결성. 자동 PASS는 형식 무결성이지 에이전트 성능이 아니다 (EVALUATION.md)."""
import hashlib
import json

from conftest import EVAL, ROOT, jl, split_rows
from schema.schemas import (
    Agent1Input, Agent1Output, Agent2Input, Agent2Output, Agent3Input, Agent3Output, PMReview, SCHEMA_VERSION,
)


def test_schema_unmodified():
    manifest = json.loads((EVAL / "manifest.json").read_text(encoding="utf-8"))
    sha = hashlib.sha256((ROOT / "schema" / "schemas.py").read_bytes()).hexdigest()
    assert sha == manifest["schema_sha256"], "공통 스키마를 수정했다. 변경 절차(공통기준 13장)를 따를 것"
    assert SCHEMA_VERSION == manifest["shared_schema_version"]


def test_core_counts():
    assert len(jl(EVAL / "requests.jsonl")) == 30
    for agent in ("agent1", "agent2"):
        assert len(split_rows(agent, "input")) == len(split_rows(agent, "gold")) == 30


def test_all_records_validate():
    for r in split_rows("agent1", "input"): Agent1Input.model_validate(r)
    for r in split_rows("agent1", "gold"): Agent1Output.model_validate(r)
    for r in split_rows("agent2", "input"): Agent2Input.model_validate(r)
    for r in split_rows("agent2", "gold"): Agent2Output.model_validate(r)
    for s in ("train", "validation", "test"):
        for r in jl(EVAL / "pm_review" / f"{s}.jsonl"): PMReview.model_validate(r)
    for r in jl(EVAL / "agent3" / "inputs.jsonl"): Agent3Input.model_validate(r)
    for r in jl(EVAL / "agent3" / "gold.jsonl"): Agent3Output.model_validate(r)


def test_a1_gold_spans_match_original_text():
    ins = {r["context"]["case_id"]: r for r in split_rows("agent1", "input")}
    for g in split_rows("agent1", "gold"):
        text = ins[g["context"]["case_id"]]["message_text"]
        for it in g["items"]:
            for sp in it["spans"]:
                assert text[sp["start"]:sp["end"]] == sp["text"], it["item_id"]


def test_split_is_not_mixed_by_parent_case():
    seen = {}
    for s in ("train", "validation", "test"):
        for r in jl(EVAL / "agent1" / s / "input.jsonl"):
            assert seen.setdefault(r["context"]["case_id"], s) == s
