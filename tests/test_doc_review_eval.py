"""문서 검토 평가셋(#89) 무결성과 채점기."""
from eval.doc_review.dataset import TYPES, load_cases, materialize
from eval.doc_review.score import score


def test_all_cases_materialize_and_use_known_types():
    cases = load_cases()
    assert len({c["id"] for c in cases}) == len(cases)
    for case in cases:
        docs = materialize(case)  # ops가 원문에 맞지 않으면 여기서 실패
        assert docs
        refs = {d["document_id"] for d in docs}
        for exp in case.get("expected", []) + case.get("forbidden", []):
            assert exp["type"] in TYPES
            assert set(exp.get("docs", [])) <= refs, (case["id"], exp)


def test_version_ops_change_only_target_item():
    case = next(c for c in load_cases() if c["id"] == "V06")
    v1, v2 = materialize(case)
    changed = [a["code"] for a, b in zip(v1["items"], v2["items"]) if a["text"] != b["text"]]
    assert changed == ["제4조"]
    assert "66,000,000" in next(i["text"] for i in v2["items"] if i["code"] == "제4조")


def test_extraction_failed_document_has_no_items():
    case = next(c for c in load_cases() if c["id"] == "M06")
    failed = [d for d in materialize(case) if d["extraction_status"] == "FAILED"]
    assert len(failed) == 1 and failed[0]["items"] == []


def test_score_perfect_empty_and_forbidden():
    cases = load_cases()
    perfect = {c["id"]: [{"type": e["type"], "docs": e["docs"], "codes": e["codes"]} for e in c.get("expected", []) if e.get("required", True)]
               for c in cases}
    good = score(cases, perfect)
    assert good["recall_required"] == 1.0 and good["forbidden_hits"] == 0 and good["unchecked_recall"] == 1.0
    assert score(cases, {})["recall_required"] == 0.0
    noisy = dict(perfect, V05=[{"type": "CHANGE", "docs": ["SOW-A-001-v1.0"], "codes": ["S-09"]}])
    assert score(cases, noisy)["forbidden_hits"] == 1
    folded = dict(perfect, V05=[{"type": "CHANGE", "docs": ["SOW-A-001-v1.0"], "codes": ["S-09"], "minor": True}])
    assert score(cases, folded)["forbidden_hits"] == 0
