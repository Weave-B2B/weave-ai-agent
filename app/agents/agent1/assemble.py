"""Deterministically assemble an Agent1 draft into its shared output schema."""

import calendar
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable

from pydantic import ValidationError

from app.agents.agent1.draft import Agent1Draft, DraftItem
from app.agents.base import AgentFailure
from app.tools.doc_tools import search_docs
from schema.schemas import Agent1Input, Agent1Output, AgentError, Item, Question, Span


@dataclass
class AssembleReport:
    """Summarize deterministic assembly decisions without source text."""

    unlocated_quotes: int = 0
    ungrounded_items: list[str] = field(default_factory=list)
    dropped_questions: int = 0
    truncated_questions: int = 0
    warnings: list[str] = field(default_factory=list)


FeatureSearch = Callable[[str, str], list[str]]


def normalize_optional_function_text(text: str) -> str:
    """Render optional request wording consistently without turning it into a requirement."""
    normalized = text.replace("(가능)", "(선택 사항)")
    normalized = normalized.replace("(선택)", "(선택 사항)")
    normalized = normalized.replace(" 가능하게", " 선택 사항으로")
    normalized = normalized.replace(" 가능하면", " 선택 사항으로")
    return normalized


def explicit_estimate_phrase(text: str) -> str | None:
    """Return the explicit estimate phrase found in source text, if any."""
    for phrase in ("견적 요청", "견적 부탁", "견적도 부탁", "견적", "비용 산출"):
        if phrase in text:
            return phrase
    return None


def normalize_estimate_request_text(text: str, intent: str) -> str:
    """Keep explicit estimate requests short regardless of LLM draft wording."""
    if intent == "estimate_request":
        return "견적 요청"
    return text


def normalize_explicit_estimate_items(inp: Agent1Input, draft: Agent1Draft) -> Agent1Draft:
    """Convert an explicit estimate request from a question draft into a request item."""
    estimate_phrase = explicit_estimate_phrase(inp.message_text)
    normalized = draft.model_copy(deep=True)
    if estimate_phrase is None:
        return normalized

    if not any(
        item.item_type == "question" and item.intent == "estimate_request"
        for item in normalized.items
    ):
        function_items = [item for item in normalized.items if item.item_type == "function"]
        target_text = function_items[0].text if function_items else "요청 기능"
        start = inp.message_text.find(estimate_phrase)
        quote = inp.message_text[start:] if start >= 0 else estimate_phrase
        normalized.items.append(
            DraftItem(
                item_type="question",
                intent="estimate_request",
                text="견적 요청",
                quotes=[quote],
                inferred=False,
            )
        )

    filtered_questions = []
    for question in normalized.questions:
        question_text = " ".join(
            part for part in (
                question.text,
                question.reason,
                question.field or "",
                question.quote or "",
            ) if part
        )
        if explicit_estimate_phrase(question_text) is None:
            filtered_questions.append(question)
    normalized.questions = filtered_questions
    return normalized


def infer_month_end_deadline(text: str, reference_date: date | None = None) -> str:
    """Add a safe, deterministic month-end deadline to recognized urgency text."""
    if not any(marker in text for marker in ("월말", "말일", "당월", "월말 정산")):
        return text

    current_date = reference_date or date.today()
    month_end = current_date.replace(day=calendar.monthrange(current_date.year, current_date.month)[1])
    days_until_month_end = max(0, (month_end - current_date).days)
    return (
        f"월말 정산 일정상 {month_end.isoformat()}까지 약 {days_until_month_end}일 이내 "
        "수정 요청으로 추정됩니다."
    )


def locate_quote(
    message_text: str,
    quote: str,
    *,
    taken: list[tuple[int, int]],
) -> tuple[int, int] | None:
    """Find an exact or whitespace-insensitive quote without fuzzy matching."""
    normalized_quote = quote.strip()
    if not normalized_quote:
        return None

    candidates = _exact_matches(message_text, normalized_quote)
    if not candidates:
        candidates = _whitespace_matches(message_text, normalized_quote)
    if not candidates:
        return None
    return next(
        (
            candidate
            for candidate in candidates
            if not any(_overlaps(candidate, used) for used in taken)
        ),
        candidates[0],
    )


def assemble(
    inp: Agent1Input,
    draft: Agent1Draft,
    *,
    feature_search: FeatureSearch | None,
    reference_date: date | None = None,
) -> tuple[Agent1Output, AssembleReport]:
    """Build and validate Agent1Output using only input and structured draft."""
    report = AssembleReport()
    draft = normalize_explicit_estimate_items(inp, draft)
    if reference_date is None:
        try:
            reference_date = date.fromisoformat(inp.received_at.strip()[:10])
        except (TypeError, ValueError):
            reference_date = date.today()
    items: list[Item] = []
    taken: list[tuple[int, int]] = []
    case_id = inp.context.case_id

    for number, draft_item in enumerate(draft.items, start=1):
        item_id = f"{case_id}-{number}"
        inferred = draft_item.inferred
        spans: list[Span] = []
        if not inferred:
            for quote in draft_item.quotes:
                location = locate_quote(inp.message_text, quote, taken=taken)
                if location is None:
                    report.unlocated_quotes += 1
                    continue
                start, end = location
                spans.append(
                    Span(
                        start=start,
                        end=end,
                        text=inp.message_text[start:end],
                    )
                )
            spans.sort(key=lambda span: span.start)
            if not spans:
                inferred = True
                report.ungrounded_items.append(item_id)
            taken.extend((span.start, span.end) for span in spans)

        intent = draft_item.intent
        if draft_item.item_type in {"deadline", "constraint", "dependency"} and intent != "constraint":
            intent = "constraint"
            report.warnings.append(f"{item_id}: intent normalized to constraint")
        elif draft_item.item_type == "function" and intent in {"constraint", "estimate_request"}:
            report.warnings.append(f"{item_id}: function intent requires review")

        if draft_item.item_type == "deadline":
            item_text = infer_month_end_deadline(draft_item.text, reference_date)
        elif draft_item.item_type == "function":
            item_text = normalize_optional_function_text(draft_item.text)
        else:
            item_text = draft_item.text

        feature_candidates: list[str] = []
        if (
            draft_item.item_type == "function"
            and feature_search is not None
            and inp.context.baseline_id is not None
        ):
            feature_candidates = feature_search(
                draft_item.text,
                inp.context.baseline_id,
            )[:3]

        items.append(
            Item(
                item_id=item_id,
                case_id=case_id,
                display_no=f"R{number}",
                item_type=draft_item.item_type,
                intent=intent,
                text=item_text,
                spans=spans,
                source="ai",
                feature_candidates=feature_candidates,
                inferred=inferred,
            )
        )

    questions = _assemble_questions(inp, draft, items, report)
    status = "NEEDS_INPUT" if any(q.blocking and q.status == "open" for q in questions) else "READY"
    payload = {
        "context": inp.context.model_dump(),
        "questions": [question.model_dump() for question in questions],
        "error": None,
        "status": status,
        "items": [item.model_dump() for item in items],
        "duplicates": [],
    }
    try:
        return Agent1Output.model_validate(payload), report
    except ValidationError as exc:
        paths = [
            ".".join(str(part) for part in error["loc"]) or "$"
            for error in exc.errors(include_input=False, include_url=False)
        ]
        raise _assembly_failure(paths) from None


def default_feature_search(text: str, baseline_id: str) -> list[str]:
    """Return up to three baseline section references matching a function item."""
    return [
        f"{result['doc_id']}:{result['section_id']}"
        for result in search_docs(text, baseline_id, top_k=3)
    ]


def _assemble_questions(
    inp: Agent1Input,
    draft: Agent1Draft,
    items: list[Item],
    report: AssembleReport,
) -> list[Question]:
    """Map, merge, limit, and validate draft questions."""
    case_id = inp.context.case_id
    item_ids = [item.item_id for item in items]
    merged: list[dict[str, Any]] = []
    merged_by_key: dict[tuple[str | None, tuple[str, ...]], dict[str, Any]] = {}

    for draft_question in draft.questions:
        mapped_item_ids: list[str] = []
        for number in draft_question.item_numbers:
            if 1 <= number <= len(item_ids):
                mapped_id = item_ids[number - 1]
                if mapped_id not in mapped_item_ids:
                    mapped_item_ids.append(mapped_id)
            else:
                report.warnings.append("question item number out of range")

        if not mapped_item_ids and not draft_question.blocking:
            report.dropped_questions += 1
            continue

        basis_refs: list[str] = []
        if draft_question.quote is not None:
            location = locate_quote(inp.message_text, draft_question.quote, taken=[])
            if location is not None:
                basis_refs.append(f"message_text[{location[0]}:{location[1]}]")
            else:
                report.unlocated_quotes += 1

        key = (draft_question.field, tuple(mapped_item_ids))
        existing = merged_by_key.get(key)
        if existing is not None:
            existing["blocking"] = existing["blocking"] or draft_question.blocking
            continue

        question_data = {
            "case_id": case_id,
            "stage": "agent1",
            "raised_by": "agent",
            "item_ids": mapped_item_ids,
            "field": draft_question.field,
            "text": draft_question.text,
            "reason": draft_question.reason,
            "basis_refs": basis_refs,
            "target": draft_question.target,
            "blocking": draft_question.blocking,
            "status": "open",
        }
        merged_by_key[key] = question_data
        merged.append(question_data)

    if len(merged) > 3:
        selected = sorted(
            enumerate(merged),
            key=lambda entry: (not entry[1]["blocking"], entry[0]),
        )[:3]
        report.truncated_questions = len(merged) - len(selected)
        merged = [question for _, question in selected]

    return [
        Question(question_id=f"{case_id}-A1Q{number}", **question_data)
        for number, question_data in enumerate(merged, start=1)
    ]


def _exact_matches(text: str, quote: str) -> list[tuple[int, int]]:
    """Return all exact quote matches, including overlapping occurrences."""
    matches: list[tuple[int, int]] = []
    start = 0
    while True:
        index = text.find(quote, start)
        if index < 0:
            return matches
        matches.append((index, index + len(quote)))
        start = index + 1


def _whitespace_matches(text: str, quote: str) -> list[tuple[int, int]]:
    """Map whitespace-insensitive matches back to original string offsets."""
    source_chars = [(index, char) for index, char in enumerate(text) if not char.isspace()]
    source_flat = "".join(char for _, char in source_chars)
    quote_flat = "".join(char for char in quote if not char.isspace())
    if not quote_flat:
        return []

    matches: list[tuple[int, int]] = []
    start = 0
    while True:
        index = source_flat.find(quote_flat, start)
        if index < 0:
            return matches
        last_index = index + len(quote_flat) - 1
        matches.append((source_chars[index][0], source_chars[last_index][0] + 1))
        start = index + 1


def _overlaps(first: tuple[int, int], second: tuple[int, int]) -> bool:
    """Return whether two half-open source ranges overlap."""
    return first[0] < second[1] and second[0] < first[1]


def _assembly_failure(paths: list[str]) -> AgentFailure:
    """Create a safe retryable assembly error containing field paths only."""
    return AgentFailure(
        AgentError(
            code="OUTPUT_INVALID",
            message=f"Agent1 output validation failed: {', '.join(paths[:10])}",
            stage="agent1",
            retryable=True,
        )
    )
