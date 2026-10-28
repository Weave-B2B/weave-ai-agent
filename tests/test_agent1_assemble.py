import re

import pytest

from conftest import EVAL, jl, split_rows
from app.agents.agent1.assemble import assemble, locate_quote
from app.agents.agent1.draft import Agent1Draft, DraftItem, DraftQuestion
from app.tools.errors import ToolInputError
from schema.schemas import Agent1Input, Agent1Output


def _input(message_text: str = "기능을 추가해주세요.") -> Agent1Input:
    """Create a small valid Agent1 request for deterministic assembler tests."""
    return Agent1Input.model_validate(
        {
            "context": {
                "project_id": "P-TEST",
                "case_id": "CASE-TEST",
                "request_revision": 1,
                "baseline_id": "BASELINE-TEST",
                "analysis_id": "ANALYSIS-TEST",
            },
            "received_at": "2026-10-28T09:00:00+09:00",
            "sender_role": "고객",
            "message_text": message_text,
        }
    )


def _item(
    text: str,
    quote: str = "기능",
    *,
    item_type: str = "function",
    intent: str = "change_request",
    inferred: bool = False,
) -> DraftItem:
    """Build one internal item draft for focused tests."""
    return DraftItem(
        item_type=item_type,
        intent=intent,
        text=text,
        quotes=[quote] if quote else [],
        inferred=inferred,
    )


def test_locate_quote_exact_whitespace_missing_and_empty() -> None:
    assert locate_quote("요청: 기능 추가", " 기능 ", taken=[]) == (4, 6)
    assert locate_quote("기능   추가", "기능 추가", taken=[]) == (0, 7)
    assert locate_quote("원문에 없음", "다른 내용", taken=[]) is None
    assert locate_quote("원문", " \t ", taken=[]) is None
    source = "이모지 😀와 한글 요청"
    start, end = locate_quote(source, "😀와 한글", taken=[]) or (-1, -1)
    assert source[start:end] == "😀와 한글"


def test_locate_quote_avoids_taken_occurrences_then_falls_back_to_first() -> None:
    assert locate_quote("같은 구절, 같은 구절", "같은 구절", taken=[(0, 5)]) == (7, 12)
    assert locate_quote(
        "같은 구절, 같은 구절",
        "같은 구절",
        taken=[(0, 5), (7, 12)],
    ) == (0, 5)


def test_item_ids_spans_order_unlocated_quotes_and_inferred_items() -> None:
    inp = _input("첫 부분 그리고 마지막")
    draft = Agent1Draft(
        items=[
            _item("두 구간을 포함하는 기능", "첫 부분"),
            DraftItem(
                item_type="function",
                intent="change_request",
                text="원문 근거가 없는 항목",
                quotes=["존재하지 않는 구절"],
            ),
            _item("명시적으로 추정한 항목", "첫 부분", inferred=True),
        ]
    )

    output, report = assemble(inp, draft, feature_search=None)

    assert [(item.item_id, item.display_no) for item in output.items] == [
        ("CASE-TEST-1", "R1"),
        ("CASE-TEST-2", "R2"),
        ("CASE-TEST-3", "R3"),
    ]
    assert [(span.start, span.end, span.text) for span in output.items[0].spans] == [(0, 4, "첫 부분")]
    assert output.items[1].inferred is True and output.items[1].spans == []
    assert output.items[2].inferred is True and output.items[2].spans == []
    assert report.unlocated_quotes == 1
    assert report.ungrounded_items == ["CASE-TEST-2"]


def test_disjoint_quotes_and_conflicting_intents_are_assembled() -> None:
    inp = _input("처음 요청, 나중 요청 그리고 일정은 금요일까지")
    draft = Agent1Draft(
        items=[
            DraftItem(
                item_type="function",
                intent="change_request",
                text="두 가지 기능 요청",
                quotes=["처음 요청", "나중 요청"],
            ),
            _item(
                "일정 제약",
                "일정은 금요일까지",
                item_type="deadline",
                intent="change_request",
            ),
            _item(
                "기능에 제약 의도가 지정된 항목",
                "처음 요청",
                item_type="function",
                intent="estimate_request",
            ),
        ]
    )

    output, report = assemble(inp, draft, feature_search=None)

    assert [span.text for span in output.items[0].spans] == ["처음 요청", "나중 요청"]
    assert [span.start for span in output.items[0].spans] == sorted(
        span.start for span in output.items[0].spans
    )
    assert output.items[1].intent == "constraint"
    assert output.items[2].intent == "estimate_request"
    assert report.warnings == [
        "CASE-TEST-2: intent normalized to constraint",
        "CASE-TEST-3: function intent requires review",
    ]


def test_questions_map_ids_basis_refs_merge_and_drop_unmapped_nonblocking() -> None:
    inp = _input("기능의 사진 수량을 알려주세요.")
    draft = Agent1Draft(
        items=[_item("사진 기능", "기능")],
        questions=[
            DraftQuestion(
                item_numbers=[1, 0, 2],
                field="photo_limit",
                text="사진 수량은 몇 개인가요?",
                reason="수량이 없습니다.",
                target="client",
                quote="사진 수량",
            ),
            DraftQuestion(
                item_numbers=[1],
                field="photo_limit",
                text="중복 질문",
                reason="같은 필드입니다.",
                target="pm",
                blocking=True,
            ),
            DraftQuestion(
                text="연결된 항목이 없는 질문",
                reason="질문에 대상이 없습니다.",
                target="pm",
            ),
        ],
    )

    output, report = assemble(inp, draft, feature_search=None)

    assert len(output.questions) == 1
    question = output.questions[0]
    assert question.question_id == "CASE-TEST-A1Q1"
    assert question.item_ids == ["CASE-TEST-1"]
    assert question.basis_refs == [f"message_text[{inp.message_text.index('사진 수량')}:{inp.message_text.index('사진 수량') + len('사진 수량')}]"]
    assert question.blocking is True
    assert question.status == "open"
    assert question.stage == "agent1"
    assert question.raised_by == "agent"
    assert output.status == "NEEDS_INPUT"
    assert report.dropped_questions == 1
    assert report.warnings == [
        "question item number out of range",
        "question item number out of range",
    ]


def test_blocking_question_without_items_is_kept_for_ambiguous_request() -> None:
    inp = _input("그거 전에 말씀드린 대로 바꿔주세요.")
    draft = Agent1Draft(
        items=[],
        questions=[
            DraftQuestion(
                text="변경할 기능과 원하는 동작을 알려주세요.",
                reason="대상이 원문에 없습니다.",
                target="pm",
                blocking=True,
                quote="그거 전에 말씀드린 대로",
            )
        ],
    )

    output, _ = assemble(inp, draft, feature_search=None)

    assert output.status == "NEEDS_INPUT"
    assert output.items == []
    assert output.questions[0].item_ids == []
    assert output.questions[0].basis_refs == [
        f"message_text[0:{len('그거 전에 말씀드린 대로')}]"
    ]


def test_questions_merge_then_limit_to_three_with_blocking_priority() -> None:
    questions = [
        DraftQuestion(
            item_numbers=[1],
            field=f"field-{index}",
            text=f"질문 {index}",
            reason="이유",
            target="pm",
        )
        for index in range(1, 5)
    ]
    questions.append(
        DraftQuestion(
            item_numbers=[1],
            field="field-5",
            text="차단 질문",
            reason="확인이 필요합니다.",
            target="pm",
            blocking=True,
        )
    )
    questions.append(
        DraftQuestion(
            item_numbers=[1],
            field="field-1",
            text="중복 질문은 첫 질문을 유지",
            reason="중복입니다.",
            target="client",
            blocking=True,
        )
    )

    output, report = assemble(
        _input("기능 추가"),
        Agent1Draft(items=[_item("기능")], questions=questions),
        feature_search=None,
    )

    assert len(output.questions) == 3
    assert [question.field for question in output.questions] == ["field-1", "field-5", "field-2"]
    assert output.questions[0].text == "질문 1"
    assert output.questions[0].blocking is True
    assert output.status == "NEEDS_INPUT"
    assert report.truncated_questions == 2


def test_empty_items_without_questions_is_ready_and_assembly_is_deterministic() -> None:
    inp = _input("안녕하세요. 일정 공유드립니다.")
    draft = Agent1Draft(items=[])

    first, first_report = assemble(inp, draft, feature_search=None)
    second, second_report = assemble(inp, draft, feature_search=None)

    assert first.status == "READY"
    assert first.items == []
    assert first.model_dump() == second.model_dump()
    assert first_report == second_report


def test_feature_candidates_only_for_function_items_and_at_most_three() -> None:
    calls: list[tuple[str, str]] = []

    def search(text: str, baseline_id: str) -> list[str]:
        calls.append((text, baseline_id))
        return ["DOC:A", "DOC:B", "DOC:C", "DOC:D"]

    inp = _input()
    draft = Agent1Draft(
        items=[
            _item("기능", "기능"),
            _item("제약", "기능", item_type="constraint", intent="constraint"),
        ]
    )

    output, _ = assemble(inp, draft, feature_search=search)

    assert calls == [("기능", "BASELINE-TEST")]
    assert output.items[0].feature_candidates == ["DOC:A", "DOC:B", "DOC:C"]
    assert output.items[1].feature_candidates == []


def test_feature_search_is_not_called_without_baseline_id() -> None:
    called = False

    def search(text: str, baseline_id: str) -> list[str]:
        nonlocal called
        called = True
        return []

    input_data = _input().model_dump()
    input_data["context"]["baseline_id"] = None
    output, _ = assemble(
        Agent1Input.model_validate(input_data),
        Agent1Draft(items=[_item("기능")]),
        feature_search=search,
    )

    assert called is False
    assert output.items[0].feature_candidates == []


def test_feature_search_input_error_propagates_unchanged() -> None:
    failure = ToolInputError("invalid baseline")

    def search(_text: str, _baseline_id: str) -> list[str]:
        raise failure

    with pytest.raises(ToolInputError) as caught:
        assemble(
            _input(),
            Agent1Draft(items=[_item("기능")]),
            feature_search=search,
        )

    assert caught.value is failure


def _draft_from_gold(gold: dict, message_text: str) -> Agent1Draft:
    """Convert one gold result to source quotes for the assembler round-trip test."""
    item_ids = [item["item_id"] for item in gold["items"]]
    draft_items = [
        {
            "item_type": item["item_type"],
            "intent": item["intent"],
            "text": item["text"],
            "quotes": [span["text"] for span in item["spans"]],
            "inferred": item.get("inferred", False),
        }
        for item in gold["items"]
    ]
    draft_questions = []
    for question in gold["questions"]:
        item_numbers = [
            item_ids.index(item_id) + 1
            for item_id in question["item_ids"]
            if item_id in item_ids
        ]
        quote = None
        for basis_ref in question["basis_refs"]:
            match = re.fullmatch(r"message_text\[(\d+):(\d+)\]", basis_ref)
            if match:
                quote = message_text[int(match.group(1)):int(match.group(2))]
                break
        draft_questions.append(
            {
                "item_numbers": item_numbers,
                "field": question["field"],
                "text": question["text"],
                "reason": question["reason"],
                "target": question["target"],
                "blocking": question["blocking"],
                "quote": quote,
            }
        )
    return Agent1Draft(items=draft_items, questions=draft_questions)


def test_dataset_agent1_gold_round_trip() -> None:
    """Validate IDs, spans, status, and item fields against all 30 gold cases."""
    inputs = {
        row["context"]["case_id"]: row
        for row in split_rows("agent1", "input")
    }
    gold_rows = split_rows("agent1", "gold")
    assert len(gold_rows) == 30
    for gold in gold_rows:
        inp = Agent1Input.model_validate(inputs[gold["context"]["case_id"]])
        draft = _draft_from_gold(gold, inp.message_text)

        assembled, _ = assemble(inp, draft, feature_search=None)
        expected = Agent1Output.model_validate(gold)

        assert assembled.status == expected.status
        assert [
            (
                item.item_id,
                item.display_no,
                item.item_type,
                item.intent,
                item.text,
                [span.model_dump() for span in item.spans],
            )
            for item in assembled.items
        ] == [
            (
                item.item_id,
                item.display_no,
                item.item_type,
                item.intent,
                item.text,
                [span.model_dump() for span in item.spans],
            )
            for item in expected.items
        ], gold["context"]["case_id"]
        assert [
            question.model_dump(exclude={"question_id", "case_id"})
            for question in assembled.questions
        ] == [
            question.model_dump(exclude={"question_id", "case_id"})
            for question in expected.questions
        ], gold["context"]["case_id"]


def test_focused_ambiguous_question_round_trip() -> None:
    """Keep the blocking item-less question from the focused contract case."""
    rows = jl(EVAL / "contract_tests" / "focused.jsonl")
    focused = next(row for row in rows if row["fixture_id"] == "A1-AMBIGUOUS")
    inp = Agent1Input.model_validate(focused["input"])
    expected = Agent1Output.model_validate(focused["expected_output"])
    draft = _draft_from_gold(expected.model_dump(), inp.message_text)

    assembled, _ = assemble(inp, draft, feature_search=None)

    assert assembled.status == expected.status == "NEEDS_INPUT"
    assert len(assembled.questions) == 1
    assert assembled.questions[0].stage == expected.questions[0].stage
    assert assembled.questions[0].blocking == expected.questions[0].blocking
    assert assembled.questions[0].item_ids == expected.questions[0].item_ids == []
