"""작업 진행 자료 스냅샷 계약: 고정 해시, 길이 제한, 코드 본문 미포함, 계약 근거와의 구분."""
import json

import pytest
from pydantic import ValidationError

from app.integrations.work_activity.models import (
    COMMENT_CHARS, DESCRIPTION_CHARS, MAX_COMMIT_MESSAGES, Commit, JiraIssue, PullRequest, WorkActivitySnapshot,
    extract_issue_keys,
)
from app.orchestration.evidence import NonContractEvidence, require_contract


def issue(key="TRIP-1", **kw):
    return JiraIssue(key=key, title=kw.pop("title", "맛집 정보 표시"), status=kw.pop("status", "진행 중"), **kw)


def pr(number=7, **kw):
    return PullRequest(repo="Weave-B2B/tripbranch", number=number, title=kw.pop("title", "TRIP-1 맛집 API 연동"),
                       state=kw.pop("state", "merged"), **kw)


def test_same_content_gives_same_hash_regardless_of_order_and_fetch_time():
    a = WorkActivitySnapshot.build(fetched_at="2026-11-27T10:00:00Z", issues=[issue("TRIP-2"), issue("TRIP-1")], pull_requests=[pr()])
    b = WorkActivitySnapshot.build(fetched_at="2026-11-28T09:00:00Z", issues=[issue("TRIP-1"), issue("TRIP-2")], pull_requests=[pr()])
    assert a.snapshot_hash == b.snapshot_hash
    assert [i.key for i in a.issues] == ["TRIP-1", "TRIP-2"]
    changed = WorkActivitySnapshot.build(fetched_at="x", issues=[issue("TRIP-1", status="완료"), issue("TRIP-2")], pull_requests=[pr()])
    assert changed.snapshot_hash != a.snapshot_hash


def test_duplicate_keeps_the_more_detailed_record():
    snap = WorkActivitySnapshot.build(fetched_at="t", issues=[issue(), issue(description="상세 설명", remaining_estimate_hours=4)])
    assert len(snap.issues) == 1 and snap.issues[0].remaining_estimate_hours == 4


def test_tampered_snapshot_is_rejected():
    data = WorkActivitySnapshot.build(fetched_at="t", issues=[issue()]).model_dump(mode="json")
    data["issues"][0]["status"] = "완료"
    with pytest.raises(ValidationError, match="snapshot_hash"):
        WorkActivitySnapshot.model_validate(data)


def test_external_text_is_clipped():
    i = issue(description="가" * 2000, recent_comments=["나" * 500] * 5)
    assert len(i.description) == DESCRIPTION_CHARS + 1 and i.description.endswith("…")
    assert len(i.recent_comments) == 3 and all(len(c) == COMMENT_CHARS + 1 for c in i.recent_comments)
    p = pr(body="다" * 3000, commit_messages=[f"TRIP-1 수정 {n}\n상세 본문" for n in range(40)], changed_files=[f"f{n}.py" for n in range(150)])
    assert len(p.body) == DESCRIPTION_CHARS + 1
    assert len(p.commit_messages) == MAX_COMMIT_MESSAGES and p.commit_messages[0] == "TRIP-1 수정 0"
    assert len(p.changed_files) == 100
    assert Commit(repo="Weave-B2B/tripbranch", sha="a1b2c3d4", message="TRIP-1 수정\n\n본문").message == "TRIP-1 수정"


def test_code_content_cannot_be_stored():
    with pytest.raises(ValidationError):
        pr(patch="@@ -1 +1 @@")
    with pytest.raises(ValidationError):
        Commit(repo="Weave-B2B/tripbranch", sha="a1b2c3d4", message="m", diff="...")


def test_issue_keys_are_found_next_to_korean_and_filtered_by_project():
    assert extract_issue_keys("TRIP-12를 수정", "feat/TRIP-3-map", "xTRIP-9", "TRIP-45a") == ["TRIP-12", "TRIP-3", "TRIP-45"]
    assert extract_issue_keys("TRIP-1 CR-01 OPS-2", projects={"TRIP", "OPS"}) == ["TRIP-1", "OPS-2"]


def test_activity_evidence_is_never_contract_evidence():
    snap = WorkActivitySnapshot.build(fetched_at="t", issues=[issue(url="https://x.atlassian.net/browse/TRIP-1")], pull_requests=[pr()],
                                      commits=[Commit(repo="Weave-B2B/tripbranch", sha="a1b2c3d4e5f6", message="TRIP-1 맛집")])
    refs = snap.evidence_refs()
    assert [(r.system, r.ref) for r in refs] == [("jira", "TRIP-1"), ("github", "Weave-B2B/tripbranch#7"),
                                                 ("github", "Weave-B2B/tripbranch@a1b2c3d4e5f6")]
    assert all(r.source_type == "activity" and r.snapshot_hash == snap.snapshot_hash for r in refs)
    with pytest.raises(NonContractEvidence):
        require_contract(refs)


def test_estimation_prompt_includes_activity_only_when_present(monkeypatch):
    from app.agents.agent3.contracts import ProvisionalAgent3Input
    from app.agents.agent3.prompts import PLAN_SYSTEM, build_user_prompt
    from test_agent3_quote_prompt import _input
    original = _input()
    inp = ProvisionalAgent3Input.model_validate({
        "context": original.context.model_dump(), "items": [r.model_dump() for r in original.items],
        "constraints": [r.model_dump() for r in original.constraints], "answers": [], "received_at": original.received_at,
        "target_item_ids": [r.item_id for r in original.targets()], "a2_result": original.a2_result.model_dump(),
        "rate_card_id": original.rate_card_id})
    assert "work_activity" not in json.loads(build_user_prompt(inp))
    inp.work_activity = WorkActivitySnapshot.build(fetched_at="t", issues=[issue(status_category="done", description="가" * 900)],
                                                   pull_requests=[pr(changed_files=[f"f{n}" for n in range(30)])])
    view = json.loads(build_user_prompt(inp))["work_activity"]
    assert view["issues"][0]["status_category"] == "done" and len(view["issues"][0]["description"]) == 501
    assert len(view["pull_requests"][0]["changed_files"]) == 20
    inp.work_activity = WorkActivitySnapshot.build(fetched_at="t", unavailable_reason="Jira 연결 안 됨")
    assert "work_activity" not in json.loads(build_user_prompt(inp))  # 자료가 없으면 보내지 않는다
    assert "계약 범위 근거나 MD의 정답으로 쓰지 않는다" in PLAN_SYSTEM


def test_run_output_records_snapshot_and_hash():
    from app.orchestration.run_agent3_payload import Agent3RunOutput, Agent3Source
    snap = WorkActivitySnapshot.build(fetched_at="t", issues=[issue()])
    source = Agent3Source(a1_result_id="a1", a2_result_id="a2", rate_card_id="RC", rate_hash="r", effort_hash="e",
                          activity_snapshot_hash=snap.snapshot_hash)
    assert source.activity_snapshot_hash == snap.snapshot_hash
    assert "work_activity" in Agent3RunOutput.model_fields
