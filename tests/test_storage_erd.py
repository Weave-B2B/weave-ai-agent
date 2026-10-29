"""② 결과 → ERD v0.6 행 변환 테스트. ERD 원본의 NOT NULL·UNIQUE·CHECK·ENUM·UUID 제약을 행 수준에서 검사한다."""
import uuid

import pytest
from conftest import split_rows
from schema.schemas import Agent2Input, Agent2Output

from app.kb.loader import doc_text, sections
from app.storage.erd import chunk_id_of, evidence_key, input_hash, sanitize_result, to_erd_rows, to_uuid
from test_agent2 import GOLD, INPUTS, _run

UUID_COLS = {
    "ai_analysis": ["ai_analysis_id", "review_version_id"],
    "item_evidence": ["item_evidence_id", "request_item_id", "document_chunk_id", "ai_analysis_id"],
    "question_answer": ["question_answer_id", "review_version_id"],
}
NOT_NULL = {
    "ai_analysis": ["ai_analysis_id", "review_version_id", "analysis_type", "analysis_status", "retry_count",
                    "analysis_created_at"],
    "item_evidence": ["item_evidence_id", "request_item_id", "document_chunk_id", "evidence_origin",
                      "location_verified", "created_at"],
    "question_answer": ["question_answer_id", "review_version_id", "question", "question_status", "created_at"],
}


def _is_uuid(v) -> bool:
    try:
        uuid.UUID(v)
        return True
    except (TypeError, ValueError):
        return False


def _rows(case_id: str, **kw):
    out, _ = _run(case_id, **kw)
    out = out.model_copy(update={"meta": out.meta.model_copy(update={"result_id": f"A2-{case_id}-test"})})
    return to_erd_rows(Agent2Input.model_validate(INPUTS[case_id]), out), out


@pytest.mark.parametrize("case_id", sorted(GOLD))
def test_rows_satisfy_erd_constraints(case_id):
    rows, out = _rows(case_id)
    for table, cols in NOT_NULL.items():
        for r in rows[table]:
            assert all(r[c] is not None for c in cols), (table, r)
            assert all(_is_uuid(r[c]) for c in UUID_COLS[table]), (table, r)
    for r in rows["question_answer"]:
        assert r["request_item_id"] is None or _is_uuid(r["request_item_id"])
        if r["question_status"] == "OPEN":                       # CHECK
            assert r["answer"] is None and r["answered_at"] is None
    keys = [evidence_key(r) for r in rows["item_evidence"]]
    assert len(keys) == len(set(keys))                           # UNIQUE
    assert {r["evidence_origin"] for r in rows["item_evidence"]} <= {"AI"}
    assert {r["conflict_type"] for r in rows["item_evidence"]} <= {None, "VERSION_CONFLICT", "UNCONFIRMED_COMMITMENT"}
    assert len(rows["ai_analysis"]) == 1
    assert len(rows["question_answer"]) == len(out.questions)


def test_evidence_chunk_ids_match_kb_chunks():
    rows, out = _rows("CR-A-001")
    kb = {s["chunk_id"] for s in sections().values()}
    assert rows["item_evidence"] and all(r["document_chunk_id"] in kb for r in rows["item_evidence"])
    assert all(e.locator.startswith("chunk:") for a in out.assessments for e in a.evidence)


def test_rows_are_deterministic_for_redelivery():
    """SQS 재전달: 같은 결과를 다시 변환하면 같은 ID → UNIQUE·upsert로 걸러진다."""
    a, _ = _rows("CR-A-001")
    b, _ = _rows("CR-A-001")
    assert [evidence_key(r) for r in a["item_evidence"]] == [evidence_key(r) for r in b["item_evidence"]]
    assert a["ai_analysis"][0]["ai_analysis_id"] == b["ai_analysis"][0]["ai_analysis_id"]


def test_result_json_has_no_debug_or_eval_fields():
    _, out = _rows("CR-A-001")
    assert out.meta.debug                                         # 원본에는 있다
    res = sanitize_result(out)
    assert res["meta"]["debug"] == {} and res["context"]["scenario_id"] is None
    assert "gold_origin" not in str(res) and "gold_ref" not in str(res)
    Agent2Output.model_validate(res)                              # 정리해도 스키마는 유효


def test_input_hash_is_stable_and_sensitive():
    inp = Agent2Input.model_validate(INPUTS["CR-A-001"])
    h = input_hash(inp)
    assert len(h) == 64 and h == input_hash(Agent2Input.model_validate(INPUTS["CR-A-001"]))
    assert h != input_hash(inp.model_copy(update={"message_text": inp.message_text + " "}))


def test_existing_uuid_passes_through():
    u = str(uuid.uuid4())
    assert to_uuid("request_item", u) == u
    assert to_uuid("request_item", "CR-A-001-1") == to_uuid("request_item", "CR-A-001-1") != u


def test_missing_result_id_is_rejected():
    out, _ = _run("CR-A-001")
    with pytest.raises(ValueError):
        to_erd_rows(Agent2Input.model_validate(INPUTS["CR-A-001"]), out)


def test_section_offsets_cover_section_text():
    """offset [start, end)로 자른 문서 텍스트에 섹션 본문이 들어 있어야 한다 (공백·표 배치 차이 제외)."""
    import re
    norm = lambda t: re.sub(r"\s+", "", t)
    bad = []
    for (d, sid), s in sections().items():
        if s["start_offset"] is None:
            continue
        assert 0 <= s["start_offset"] < s["end_offset"]
        seg = norm(doc_text(d)[s["start_offset"]:s["end_offset"]])
        body = norm(s["text"])
        if not (body[:10] in seg and body[-8:] in seg):
            bad.append(f"{d}:{sid}")
    assert len(bad) <= 6, bad                                      # 표 레이아웃 섹션 일부는 줄이 섞여 있다


def _erd_tables() -> dict[str, dict[str, bool]]:
    """docs/data_dictionary/references/cn_erd_v06_export.sql → {table: {column: NOT NULL}}."""
    import re
    from pathlib import Path
    sql = (Path(__file__).parents[1] / "docs/data_dictionary/references/cn_erd_v06_export.sql").read_text(encoding="utf-8")
    tables = {}
    for m in re.finditer(r'CREATE TABLE\s+"?(\w+)"?\s*\((.*?)\n\);', sql, re.S):
        cols = {}
        for line in m.group(2).split("\n"):
            c = re.match(r'\s*"?(\w+)"?\s+([A-Za-z]+[^,]*?)(,|$)', line)
            if c and c.group(1).upper() not in ("CONSTRAINT", "PRIMARY"):
                cols[c.group(1)] = "NOT NULL" in c.group(2).upper()
        tables[m.group(1)] = cols
    return tables


@pytest.mark.parametrize("case_id", sorted(GOLD))
def test_rows_match_erd_sql_export(case_id):
    """CN ERD v0.6 내보내기 SQL과 컬럼 이름·NOT NULL을 직접 대조한다 (원본이 바뀌면 여기서 깨진다)."""
    tables = _erd_tables()
    rows, _ = _rows(case_id)
    for table, rs in rows.items():
        cols = tables[table]
        for r in rs:
            assert set(r) <= set(cols), (table, set(r) - set(cols))
            assert all(r.get(c) is not None for c, nn in cols.items() if nn), (table, r)
