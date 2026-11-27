"""문서 검토 에이전트 (#91) — FakeLlm으로 울타리·빠짐없음·기록 확인."""
from app.agents.doc_review import DocReviewAgent, DocReviewInput
from app.llm.fake import FakeLlm
from eval.doc_review.dataset import load_cases, materialize


def _case(cid):
    case = next(c for c in load_cases() if c["id"] == cid)
    return case, DocReviewInput(as_of=case["as_of"], documents=materialize(case), prior_judgments=case.get("prior_judgments", []))


def _keys(inp):
    return [f"{d['document_id']}@v{d['version_no']}#{i['item_id']}" for d in inp.documents for i in d["items"]]


def test_version_case_records_change_and_only_changed_items_remain():
    _, inp = _case("V06")
    target = next(k for k in _keys(inp) if k.startswith("CTR-A-001-v1.0@v2") and "B1-00005" in k)
    llm = FakeLlm([
        {"thought": "제4조 금액이 바뀜", "action": "record_finding", "keys": [], "codes": [],
         "finding": {"type": "CHANGE", "items": [target], "title": "계약 대금 변경", "explanation": "60,000,000원에서 66,000,000원으로 증액",
                     "confidence": "high", "change": "modified"}},
        {"thought": "끝", "action": "finish", "keys": [], "codes": []},
    ])
    result = DocReviewAgent(llm).run(inp)
    assert [(f.type, f.codes) for f in result.findings] == [("CHANGE", ["제4조"])]
    assert result.unchecked == [] and result.coverage["checked"] == result.coverage["items"]
    assert "<version_diff>" in llm.calls[0]["user"]       # 규칙 도구 결과를 관찰에 싣는다


def test_finish_is_rejected_until_items_checked_then_unchecked_marked():
    _, inp = _case("M08")
    keys = _keys(inp)
    llm = FakeLlm([{"thought": "끝", "action": "finish", "keys": [], "codes": []}] * 3)
    result = DocReviewAgent(llm).run(inp)
    assert len(llm.calls) == 3                            # 두 번 거절 후 세 번째에 끝냄
    assert "rejected" in str(result.trace[0]) and len(result.unchecked) == len(keys)

    llm = FakeLlm([{"thought": "모두 확인", "action": "mark_checked", "keys": keys + ["X@v1#nope"], "codes": []},
                   {"thought": "끝", "action": "finish", "keys": [], "codes": []}])
    result = DocReviewAgent(llm).run(inp)
    assert result.unchecked == [] and result.trace[0]["result"]["unknown_keys"] == ["X@v1#nope"]


def test_failed_document_becomes_system_unchecked_finding_and_invented_keys_rejected():
    _, inp = _case("M06")
    keys = _keys(inp)
    llm = FakeLlm([
        {"thought": "가짜 키", "action": "record_finding", "keys": [], "codes": [],
         "finding": {"type": "MISSING", "items": ["QUO-A-001-v1.0@v1#B1-00002"], "title": "x", "explanation": "x", "confidence": "low"}},
        {"thought": "확인", "action": "mark_checked", "keys": keys, "codes": []},
        {"thought": "끝", "action": "finish", "keys": [], "codes": []},
    ])
    result = DocReviewAgent(llm).run(inp)
    assert "error" in result.trace[0]["result"]           # 추출 실패 문서 항목 키는 쓸 수 없다
    assert [(f.type, f.docs, f.source) for f in result.findings] == [("UNCHECKED", ["QUO-A-001-v1.0"], "system")]


def test_prior_judgment_tool_filters_by_document_and_code():
    _, inp = _case("M07")
    llm = FakeLlm([{"thought": "기존 판단 조회", "action": "prior_judgments", "document_id": "FSP-A-001-v1.0", "codes": ["A-F16"], "keys": []},
                   {"thought": "확인", "action": "mark_checked", "keys": _keys(inp), "codes": []},
                   {"thought": "끝", "action": "finish", "keys": [], "codes": []}])
    result = DocReviewAgent(llm).run(inp)
    assert result.trace[0]["result"]["judgments"][0]["change_request"] == "CR-07"


def test_code_reuse_change_is_split_into_removed_and_added():
    _, inp = _case("V09")
    old = next(k for k in _keys(inp) if k.startswith("FSP-A-001-v1.0@v1") and k.endswith("#B1-00011"))
    llm = FakeLlm([{"thought": "대체됨", "action": "record_finding", "keys": [], "codes": [],
                    "finding": {"type": "CHANGE", "items": [old], "title": "A-F09 대체", "explanation": "결제 오류 처리가 위시리스트로 대체",
                                "confidence": "high", "change": "modified"}},
                   {"thought": "끝", "action": "finish", "keys": [], "codes": []}])
    result = DocReviewAgent(llm).run(inp)
    assert sorted(f.change for f in result.findings) == ["added", "removed"]
    assert result.unchecked == []


def test_read_items_kept_once_outside_history_window():
    _, inp = _case("M03")
    keys = _keys(inp)
    reads = [{"thought": "읽기", "action": "read_items", "keys": [keys[0]], "codes": []}] + \
            [{"thought": "다시", "action": "read_items", "keys": [keys[0]], "codes": []}] + \
            [{"thought": "찾기", "action": "find_same_items", "query": "계약 대금", "keys": [], "codes": []}] * 11
    llm = FakeLlm(reads + [{"thought": "확인", "action": "mark_checked", "keys": keys, "codes": []},
                           {"thought": "끝", "action": "finish", "keys": [], "codes": []}])
    result = DocReviewAgent(llm).run(inp)
    assert result.trace[1]["result"] == {"read": [], "already_read": [keys[0]]}   # 같은 항목은 다시 싣지 않는다
    last_obs = llm.calls[-1]["user"]
    assert keys[0] in last_obs.split("<read_items>")[1].split("</read_items>")[0]  # 기록 창을 벗어나도 원문은 남는다
