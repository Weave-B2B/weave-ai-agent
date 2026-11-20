"""엑셀 일정 자료 + Agent 3 작업 행 → Backend 일정 계산(schedule-v1) 입력 변환과 결과 연결.

계산 자체는 Backend 골든 벡터(tests/fixtures/schedule_v1_golden.json과 같은 파일)로 검증한다.
여기서는 변환 결과가 Backend 골든 입력과 같은지, 미배정·장애·문서·승인 연결을 확인한다.
"""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agents.agent3.contracts import ProvisionalLine, ScheduleHint
from app.tools import schedule_plan
from app.tools.backend_calc import BackendCalcError
from app.tools.errors import ToolInputError

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = json.loads((ROOT / "tests/fixtures/schedule_v1_golden.json").read_text(encoding="utf-8"))
SAMPLE = json.loads((ROOT / "data/scenarios/schedule-v1-sample.json").read_text(encoding="utf-8"))
SAMPLE_REQUEST = GOLDEN["cases"][0]["request"]


def workbook_data():
    """schedule-v1-sample을 impact_workbook.parse_workbook 출력 형태로 옮긴 것."""
    return {
        "tasks": [{"name": t["id"], "owner": t["person_id"], "remaining_hours": t["remaining_hours"],
                   "predecessors": t["predecessors"]} for t in SAMPLE["baseline_tasks"]],
        "people": [{"name": p["id"], "daily_hours": p["daily_available_hours"], "available_from": p["available_from"]}
                   for p in SAMPLE["people"]],
        "calendar": {"start_date": "2026-11-23", "planned_end": SAMPLE["rules"]["current_plan_end"],
                     "working_weekdays": "월,화,수,목,금",
                     "exceptions": [{"person": "전체", "date": d, "hours": 0} for d in SAMPLE["calendar"]["holidays"]]},
        "missing": [],
    }


def review_lines(review_hint=None, qa_hint=None):
    review = ProvisionalLine(task="리뷰 API 개발", item_ids=["I1"], role="BE", md=2.0, assumption="",
        schedule_hint=ScheduleHint(assignee="be-1", after=["T-BE"], before=["T-QA"]) if review_hint is None else review_hint)
    qa = ProvisionalLine(task="리뷰 통합 테스트 추가", item_ids=["I1"], role="QA", md=0.5, assumption="",
        schedule_hint=ScheduleHint(extends_task="T-QA") if qa_hint is None else qa_hint)
    return [("E1L1", review), ("E1L2", qa)]


def output(lines, data=None, hours_per_md="8", total_hours="20"):
    return SimpleNamespace(
        impact_workbook={"source": {"documentVersionId": "v1"}, "data": data or workbook_data()},
        pricing=SimpleNamespace(hours_per_md=hours_per_md, total_hours=total_hours),
        estimates=[SimpleNamespace(lines=[line for _, line in lines])])


def test_workbook_and_hints_become_the_backend_golden_sample_request():
    request, problems = schedule_plan.build_request(workbook_data(), review_lines(), "8", "20")
    assert problems == []
    expected = copy.deepcopy(SAMPLE_REQUEST)
    expected["change"]["added"][0]["id"] = "E1L1"  # 계산 행 키를 작업 ID로 쓴다
    assert _same_request(request, expected)


def _same_request(actual, expected):
    num = lambda v: None if v is None else float(v)
    assert actual["start_date"] == expected["start_date"] and actual["planned_end"] == expected["planned_end"]
    assert actual["workdays"] == expected["workdays"]
    assert [(e["person"], e["date"], num(e["hours"])) for e in actual["exceptions"]] == \
        [(e["person"], e["date"], num(e["hours"])) for e in expected["exceptions"]]
    assert [(p["id"], num(p["daily_hours"]), p["available_from"]) for p in actual["people"]] == \
        [(p["id"], num(p["daily_hours"]), p["available_from"]) for p in expected["people"]]
    assert [(t["id"], t["person"], num(t["hours"]), t["predecessors"]) for t in actual["baseline_tasks"]] == \
        [(t["id"], t["person"], num(t["hours"]), t["predecessors"]) for t in expected["baseline_tasks"]]
    a, e = actual["change"], expected["change"]
    assert [(x["id"], x["person"], num(x["hours"]), x["predecessors"], x["successors"]) for x in a["added"]] == \
        [(x["id"], x["person"], num(x["hours"]), x["predecessors"], x["successors"]) for x in e["added"]]
    assert [(x["task_id"], num(x["hours"])) for x in a["extended"]] == [(x["task_id"], num(x["hours"])) for x in e["extended"]]
    assert num(a["change_hours"]) == num(e["change_hours"])
    return True


@pytest.mark.parametrize("hint, reason", [
    (ScheduleHint(), "담당자 미지정"),
    (ScheduleHint(assignee="be-9"), "엑셀에 없는 담당자"),
    (ScheduleHint(assignee="be-1", after=["없는 작업"]), "엑셀에 없는 선행·후속 작업"),
    (ScheduleHint(extends_task="T-QA", assignee="be-1"), "함께 지정할 수 없음"),
])
def test_any_unmatched_line_skips_the_whole_schedule(hint, reason):
    request, problems = schedule_plan.build_request(workbook_data(), review_lines(review_hint=hint), "8", "20")
    assert request is None and len(problems) == 1 and reason in problems[0] and "리뷰 API 개발(BE)" in problems[0]


def test_missing_hint_is_unassigned():
    line = ProvisionalLine(task="리뷰 API 개발", item_ids=["I1"], role="BE", md=2.0, assumption="")
    request, problems = schedule_plan.build_request(workbook_data(), [("E1L1", line)], "8", "16")
    assert request is None and "담당자 미지정" in problems[0]


def test_workdays_text_is_validated():
    assert schedule_plan.workdays("월, 화,수 목,금") == [1, 2, 3, 4, 5]
    with pytest.raises(ToolInputError):
        schedule_plan.workdays("월,월")
    with pytest.raises(KeyError):
        schedule_plan.workdays("Mon")


def test_calculate_plan_calls_backend_once_and_keeps_its_result(monkeypatch):
    from app.tools import backend_calc
    seen = []

    class Client:
        def schedule(self, body):
            seen.append(body)
            return {"formula_version": "schedule-v1", "changed": {"end": "2026-12-01"}, "snapshot": {"snapshot_id": "s"}}

    monkeypatch.setattr(backend_calc, "calculation_client", lambda _: Client())
    detail = schedule_plan.calculate_plan(output(review_lines()), "11111111-1111-1111-1111-111111111111")
    assert detail["plan"]["changed"]["end"] == "2026-12-01" and detail["unavailable_reason"] is None
    assert len(seen) == 1 and seen[0]["change"]["change_hours"] == "20"


def test_calculate_plan_without_workbook_keeps_existing_flow():
    out = output(review_lines())
    out.impact_workbook = None
    assert schedule_plan.calculate_plan(out, "rate") is None


def test_calculate_plan_reports_backend_failure_instead_of_guessing(monkeypatch):
    from app.tools import backend_calc

    class Down:
        def schedule(self, body):
            raise BackendCalcError("백엔드 계산 API 오류 503")

    monkeypatch.setattr(backend_calc, "calculation_client", lambda _: Down())
    detail = schedule_plan.calculate_plan(output(review_lines()), "11111111-1111-1111-1111-111111111111")
    assert detail["plan"] is None and "503" in detail["unavailable_reason"]


def test_fixture_calculator_has_no_schedule_and_says_so(monkeypatch):
    from app.tools import backend_calc
    monkeypatch.setattr(backend_calc, "calculation_client", lambda _: object())
    detail = schedule_plan.calculate_plan(output(review_lines()), "RC-SYNTH-2026H2-v2")
    assert detail["plan"] is None and "Backend" in detail["unavailable_reason"]


def test_customer_lines_use_backend_values_and_state_the_assumption():
    plan = {"estimated_end_date": "2026-12-01", "baseline": {"end": "2026-11-26"}, "planned_end": "2026-11-26",
            "impact_business_days": 2, "impact_vs_baseline_business_days": 2}
    text = "\n".join(schedule_plan.customer_lines({"plan": plan}))
    assert "예상 완료일: 2026-12-01" in text and "2026-11-26 대비 2영업일" in text and "전제로 한 예상" in text
    plan.update(planned_end=None, impact_business_days=None)
    assert "현재 계획 완료일 미입력" in "\n".join(schedule_plan.customer_lines({"plan": plan}))
    assert schedule_plan.customer_lines({"plan": None, "unavailable_reason": "x"}) == []


def test_backend_client_validates_schedule_response_shape():
    import httpx
    from app.tools.backend_calc import CalcClient

    def handler(request):
        if request.url.path.endswith("/schedules"):
            return httpx.Response(200, json={"formula_version": "schedule-v1", "changed": {}, "snapshot": {"snapshot_id": "a"}})
        return httpx.Response(200, json={"valid": True, "snapshot_id": "a"})

    client = CalcClient("http://b", "t", http=httpx.Client(base_url="http://b", transport=httpx.MockTransport(handler)))
    plan = client.schedule(SAMPLE_REQUEST)
    assert client.validate_schedule(plan)["valid"]
    bad = CalcClient("http://b", "t", http=httpx.Client(base_url="http://b",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"status": "OK"}))))
    with pytest.raises(BackendCalcError):
        bad.schedule(SAMPLE_REQUEST)


def test_golden_file_is_identical_to_backend_copy():
    backend = ROOT.parent / "weave-backend/src/test/resources/calc/schedule_v1_golden.json"
    if not backend.exists():
        pytest.skip("weave-backend 저장소가 옆에 없음")
    assert backend.read_bytes() == (ROOT / "tests/fixtures/schedule_v1_golden.json").read_bytes()


def test_real_xlsx_parse_output_converts_without_reshaping():
    """실제 XLSX 파서 출력(test_impact_workbook의 양식)을 그대로 변환할 수 있는지 확인한다."""
    from test_impact_workbook import workbook
    from app.tools.impact_workbook import parse_workbook
    data = parse_workbook(workbook())
    task, person = data["tasks"][0], data["people"][0]
    line = ProvisionalLine(task="추가 화면", item_ids=["I1"], role="FE", md=1.0, assumption="",
                           schedule_hint=ScheduleHint(assignee=person["name"], after=[task["name"]]))
    request, problems = schedule_plan.build_request(data, [("E1L1", line)], "8", "8")
    assert problems == [] and request["start_date"] == data["calendar"]["start_date"]
    assert request["baseline_tasks"][0]["id"] == task["name"] and request["people"][0]["id"] == person["name"]
    assert request["change"]["added"][0] == {"id": "E1L1", "person": person["name"], "hours": "8",
                                             "predecessors": [task["name"]], "successors": []}
