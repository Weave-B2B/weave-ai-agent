"""승인된 일정·공수 엑셀과 Agent 3 작업 행을 일정 계산(schedule-v1) 입력으로 바꾼다.

계산(배정·완료일·일정 영향)은 Backend `POST /internal/v1/estimates/schedules`만 한다. 여기서는
1) 엑셀의 기존 작업·담당자·근무일을 Backend 입력 형식으로 옮기고,
2) 각 작업 행의 담당자·선행/후속 작업(모델 제안)을 엑셀 이름과 대조해 새 작업 또는 기존 작업 증가로 붙인다.
담당자가 없거나 엑셀에 없는 이름을 가리키는 행이 하나라도 있으면 일정을 계산하지 않고 사유를 남긴다
(계산식 v1: 인력 미배정이면 공수는 제안해도 일정은 확정하지 않는다).
"""
from __future__ import annotations

from decimal import Decimal

from app.tools.errors import ToolInputError

FORMULA_VERSION = "schedule-v1"
_WEEKDAYS = {"월": 1, "화": 2, "수": 3, "목": 4, "금": 5, "토": 6, "일": 7}


def _num(value) -> str:
    n = Decimal(str(value))
    return "0" if n == 0 else format(n.normalize(), "f")


def workdays(text: str) -> list[int]:
    import re
    days = [_WEEKDAYS[d] for d in re.split(r"[,\s]+", text or "") if d]
    if not days or len(days) != len(set(days)):
        raise ToolInputError("근무요일은 월,화,수,목,금 형태로 입력해주세요.")
    return sorted(days)


def hints_problem(hint, data) -> str | None:
    """작업 행의 일정 힌트가 엑셀과 맞지 않으면 사유, 맞으면 None."""
    if hint is None:
        return "담당자 미지정"
    tasks = {t["name"] for t in data["tasks"]}
    people = {p["name"] for p in data["people"]}
    if hint.extends_task:
        if hint.extends_task not in tasks:
            return f"엑셀에 없는 기존 작업: {hint.extends_task}"
        if hint.assignee or hint.after or hint.before:
            return "기존 작업 증가 행에는 담당자·선행·후속 작업을 함께 지정할 수 없음"
        return None
    if not hint.assignee:
        return "담당자 미지정"
    if hint.assignee not in people:
        return f"엑셀에 없는 담당자: {hint.assignee}"
    unknown = [n for n in [*hint.after, *hint.before] if n not in tasks]
    if unknown:
        return "엑셀에 없는 선행·후속 작업: " + ", ".join(unknown)
    return None


def build_request(data: dict, lines: list[tuple[str, object]], hours_per_md, total_hours) -> tuple[dict | None, list[str]]:
    """(요청, 미배정 사유 목록). lines는 (계산 행 키, ProvisionalLine) 목록이다."""
    problems, added, extended = [], [], {}
    hours_per_md = Decimal(str(hours_per_md))
    for key, line in lines:
        hint = getattr(line, "schedule_hint", None)
        problem = hints_problem(hint, data)
        if problem:
            problems.append(f"{line.task}({line.role}): {problem}")
            continue
        hours = Decimal(str(line.md)) * hours_per_md
        if hint.extends_task:
            extended[hint.extends_task] = extended.get(hint.extends_task, Decimal(0)) + hours
        else:
            added.append({"id": key, "person": hint.assignee, "hours": _num(hours),
                          "predecessors": list(hint.after), "successors": list(hint.before)})
    if problems:
        return None, problems
    calendar = data["calendar"]
    request = {
        "start_date": calendar["start_date"], "planned_end": calendar.get("planned_end"),
        "workdays": workdays(calendar["working_weekdays"]),
        "exceptions": [{"person": None if e["person"] == "전체" else e["person"], "date": e["date"],
                        "hours": _num(e["hours"])} for e in calendar.get("exceptions", [])],
        "people": [{"id": p["name"], "daily_hours": _num(p["daily_hours"]), "available_from": p.get("available_from")}
                   for p in data["people"]],
        "baseline_tasks": [{"id": t["name"], "person": t["owner"], "hours": _num(t["remaining_hours"]),
                            "predecessors": list(t["predecessors"])} for t in data["tasks"]],
        "change": {"added": added, "extended": [{"task_id": k, "hours": _num(v)} for k, v in extended.items()],
                   "change_hours": _num(total_hours)},
    }
    return request, []


def calculate_plan(out, rate_card_id: str) -> dict | None:
    """요청 전체 금액 계산이 끝난 산정 결과에 일정 계산 결과(또는 미계산 사유)를 붙일 dict를 돌려준다.

    엑셀 자료가 없으면 None(기존 흐름 유지). 계산기 장애는 다른 값으로 숨기지 않고 사유로 남긴다.
    """
    workbook = getattr(out, "impact_workbook", None) or {}
    data = workbook.get("data")
    if not data or out.pricing is None:
        return None
    lines = [(f"E{n + 1}L{m + 1}", line) for n, e in enumerate(out.estimates) for m, line in enumerate(e.lines)]
    request, problems = build_request(data, lines, out.pricing.hours_per_md, out.pricing.total_hours)
    if request is None:
        return {"formula_version": FORMULA_VERSION, "plan": None,
                "unavailable_reason": "인력 미배정 또는 엑셀과 맞지 않는 작업이 있어 완료일·일정 영향을 계산하지 않음",
                "unassigned": problems}
    from app.tools.backend_calc import BackendCalcError, calculation_client
    try:
        client = calculation_client(rate_card_id)
        if not hasattr(client, "schedule"):
            raise BackendCalcError("일정 계산은 Backend 공식 계산기에서만 지원합니다.")
        plan = client.schedule(request)
    except (ToolInputError, BackendCalcError) as err:
        return {"formula_version": FORMULA_VERSION, "plan": None, "unavailable_reason": str(err)[:300], "unassigned": []}
    return {"formula_version": FORMULA_VERSION, "plan": plan, "unavailable_reason": None, "unassigned": []}


def customer_lines(plan_detail: dict | None) -> list[str]:
    """고객 문서용 일정 사실. 계산된 완료일·영향만 쓰고, 근거가 엑셀 가정임을 밝힌다."""
    plan = (plan_detail or {}).get("plan")
    if not plan:
        return []
    lines = [f"- 예상 완료일: {plan['estimated_end_date']} (현재 계획 기준 {plan['baseline']['end']})"]
    if plan.get("impact_business_days") is not None:
        lines.append(f"- 일정 영향: 현재 계획 완료일 {plan['planned_end']} 대비 {plan['impact_business_days']}영업일")
    else:
        lines.append(f"- 일정 영향: 현재 계획 완료일 미입력. 기존 작업 일정 대비 {plan['impact_vs_baseline_business_days']}영업일")
    lines.append("- 위 일정은 공유된 작업 계획·인력 가용 시간을 전제로 한 예상이며 착수 일정 확정 후 다시 안내드립니다.")
    return lines
