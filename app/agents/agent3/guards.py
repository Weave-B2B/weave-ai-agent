"""③ 가드 규칙: LLM 금액 거부, calc_cost 호출로만 totals 채우기."""
from schema.schemas import Agent3Output, Question, Totals

from app.tools.calc_cost import calc_cost


class OutputInvalid(ValueError):
    """LLM 원출력에 금액이 있음 → 오류 코드 OUTPUT_INVALID (1회 재요청 후 ERROR)."""


def reject_llm_amounts(raw: dict) -> None:
    """LLM 원출력(dict)에 금액 필드가 있으면 검증 실패. 금액은 calc_cost만 채운다."""
    for e in raw.get("estimates", []):
        if e.get("totals") is not None or e.get("totals_source") is not None:
            raise OutputInvalid("LLM 출력에 totals 포함")
    if (raw.get("draft") or {}).get("cost") is not None:
        raise OutputInvalid("LLM 출력 draft.cost 포함")


def finalize_estimates(out: Agent3Output, rate_card_id: str) -> Agent3Output:
    """draft 추정마다 calc_cost로 totals를 붙인다. 단가 누락은 오류가 아니라 NEEDS_INPUT + 질문이다.
    부분 합계를 완성된 총액처럼 보여 주지 않는다."""
    data = out.model_dump()
    missing: list[str] = []
    for e in data["estimates"]:
        e["totals"], e["totals_source"] = None, None
        if e["status"] != "draft":
            continue
        res = calc_cost(e["lines"], rate_card_id)  # 입력 오류는 ToolInputError(INVALID_INPUT)로 올라감
        if res.get("status") == "NEEDS_INPUT":
            missing += res["missing"]
            continue
        e["totals"] = Totals(**res).model_dump()
        e["totals_source"] = "calc_cost"
    if missing:
        case_id = out.context.case_id
        q = Question(question_id=f"{case_id}-A3Q9", case_id=case_id, stage="agent3", item_ids=sorted({i for e in data["estimates"] for i in e["item_ids"]}),
                     field=",".join(sorted(set(missing))), text="단가표에 없는 직군의 단가를 알려주세요.",
                     reason="단가 누락으로 금액을 계산할 수 없음", basis_refs=sorted(set(missing)), target="pm", blocking=True)
        data["questions"] = data["questions"] + [q.model_dump()]
        data["status"] = "NEEDS_INPUT"
        data["draft"] = None
    return Agent3Output.model_validate(data)
