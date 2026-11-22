"""회신 맥락 분석(#72): 이전 메일·기존 항목을 맥락으로 주고 항목마다 action을 정한다."""

from app.agents.agent1.assemble import assemble
from app.agents.agent1.draft import Agent1Draft, DraftItem
from app.agents.agent1.prompts import build_user_prompt
from schema.schemas import Agent1Input

MESSAGE = "영문 상세페이지는 언어 선택으로 전환해 주세요. 검색 필터 건은 빼주세요. 카카오 로그인도 추가해 주세요."


def _input(with_context: bool = True) -> Agent1Input:
    data = {
        "context": {"project_id": "P", "case_id": "CR", "request_revision": 1, "baseline_id": None},
        "received_at": "2026-11-23T09:00:00+09:00",
        "sender_role": "고객",
        "message_text": MESSAGE,
    }
    if with_context:
        data["thread_context"] = [{"message_id": "m1", "received_at": "2026-11-20T09:00:00+09:00",
                                   "sender_role": "고객", "text": "영문 상세페이지와 검색 필터를 추가해 주세요."}]
        data["existing_items"] = [
            {"item_id": "item-en", "item_type": "function", "text": "영문 상품 상세페이지 추가"},
            {"item_id": "item-filter", "item_type": "function", "text": "가격·브랜드 검색 필터 적용"},
        ]
    return Agent1Input.model_validate(data)


def _draft_item(text, quote, action="new", target=None):
    return DraftItem(item_type="function", intent="change_request", text=text, quotes=[quote],
                     change_kind="feature_add", action=action, target_item_no=target)


def test_action과_대상_번호를_기존_항목_ID로_바꾼다():
    draft = Agent1Draft(items=[
        _draft_item("영문 상세페이지를 언어 선택으로 전환", "영문 상세페이지는 언어 선택으로 전환해 주세요", "update", 1),
        _draft_item("검색 필터 철회", "검색 필터 건은 빼주세요", "retract", 2),
        _draft_item("카카오 로그인 추가", "카카오 로그인도 추가해 주세요"),
    ])

    out, report = assemble(_input(), draft, feature_search=None)

    assert [(i.action, i.target_item_id) for i in out.items] == [
        ("update", "item-en"), ("retract", "item-filter"), ("new", None)]
    assert all(i.spans for i in out.items)                       # span은 이번 회신 원문 기준
    assert not report.warnings or all("target invalid" not in w for w in report.warnings)


def test_대상_번호가_없거나_범위를_벗어나면_new로_내린다():
    draft = Agent1Draft(items=[
        _draft_item("영문 전환", "영문 상세페이지는 언어 선택으로 전환해 주세요", "update", 9),
        _draft_item("필터 철회", "검색 필터 건은 빼주세요", "retract", None),
    ])

    out, report = assemble(_input(), draft, feature_search=None)

    assert [(i.action, i.target_item_id) for i in out.items] == [("new", None), ("new", None)]
    assert sum("target invalid" in w for w in report.warnings) == 2


def test_기존_항목이_없는_첫_메일은_action을_비운다():
    draft = Agent1Draft(items=[_draft_item("영문 전환", "영문 상세페이지는 언어 선택으로 전환해 주세요", "update", 1)])

    out, _ = assemble(_input(with_context=False), draft, feature_search=None)

    assert out.items[0].action is None and out.items[0].target_item_id is None
    assert "action" not in out.items[0].model_dump(exclude_none=True)   # 기존 산출물·데이터셋과 동일


def test_맥락이_없으면_회신_블록을_넣지_않는다():
    prompt = build_user_prompt(_input(with_context=False))
    assert "<thread_context>" not in prompt and "<existing_items>" not in prompt and "<thread_guide>" not in prompt


def test_회신이면_이전_메일과_번호_붙은_기존_항목을_프롬프트에_넣는다():
    prompt = build_user_prompt(_input())
    assert "<thread_guide>" in prompt
    assert "영문 상세페이지와 검색 필터를 추가해 주세요." in prompt
    assert "1. function | 영문 상품 상세페이지 추가" in prompt
    assert "2. function | 가격·브랜드 검색 필터 적용" in prompt
    assert prompt.index("</existing_items>") < prompt.index("<message_text>")


def test_요약은_결과에_담고_빈_요약은_비운다():
    draft = Agent1Draft(items=[_draft_item("카카오 로그인 추가", "카카오 로그인도 추가해 주세요")],
                        summary="  영문 상세페이지를 언어 선택 방식으로 바꾸고 카카오 로그인을 추가해 달라는 요청.  ")
    out, _ = assemble(_input(), draft, feature_search=None)
    assert out.summary == "영문 상세페이지를 언어 선택 방식으로 바꾸고 카카오 로그인을 추가해 달라는 요청."

    out, _ = assemble(_input(), Agent1Draft(items=[], summary="   "), feature_search=None)
    assert out.summary is None


def test_모든_메일_프롬프트에_요약_지침이_들어간다():
    assert "<summary_guide>" in build_user_prompt(_input(with_context=False))
    assert "<summary_guide>" in build_user_prompt(_input())
