"""Jira 조회 도구: JQL 생성·응답 파싱·허용 범위·조회 상한·조사 단계·총괄 연결. 실제 Jira는 호출하지 않는다."""
import json
from types import SimpleNamespace

import httpx
import pytest

from app.agents.agent3.activity_probe import ActivityProbe, ProbeStep
from app.integrations.work_activity.base import WorkActivityError, WorkActivityTools
from app.integrations.work_activity.factory import build_work_activity_source
from app.integrations.work_activity.fake import FakeWorkActivitySource
from app.integrations.work_activity.jira_cloud import JiraCloudSource, adf_to_text, build_search_jql
from test_agent3_agent import a3_payload  # noqa: F401 — 총괄 흐름 fixture가 쓴다
from test_run_supervisor import setup  # noqa: F401


# ---- JQL ----
def test_jql_is_built_by_server_with_escaped_text_and_allowed_projects():
    jql = build_search_jql('맛집 "API" (OR) project=X*', ["TRIP", "OPS"], status_category="in_progress",
                           updated_since="2026-11-01")
    assert jql == ('project in (OPS, TRIP) AND summary ~ "맛집 API OR project=X" AND statusCategory = "In Progress" '
                   'AND updated >= "2026-11-01" ORDER BY updated DESC')
    assert 'AND text ~ "맛집"' in build_search_jql("맛집", ["TRIP"], field="text")
    with pytest.raises(WorkActivityError):
        build_search_jql("맛집", ["TRIP"], field="description")
    with pytest.raises(WorkActivityError):
        build_search_jql("맛집", [])
    with pytest.raises(WorkActivityError):
        build_search_jql('"()"', ["TRIP"])  # 문법 문자만 있는 검색어


def test_adf_document_becomes_plain_text():
    doc = {"type": "doc", "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": "반경 500m "}, {"type": "text", "text": "맛집"}]},
        {"type": "bulletList", "content": [{"type": "listItem", "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": "개수 미정"}]}]}]}]}
    assert adf_to_text(doc).split("\n")[:2] == ["반경 500m 맛집", "개수 미정"]


# ---- Jira Cloud 어댑터 (가짜 HTTP 응답) ----
ISSUE = {"key": "TRIP-118", "fields": {
    "summary": "주변 맛집 정보 API 연동 검토", "status": {"name": "진행 중", "statusCategory": {"key": "indeterminate"}},
    "issuetype": {"name": "Task"}, "assignee": {"displayName": "이도윤"}, "parent": {"key": "TRIP-100"},
    "labels": ["외부연동"], "components": [{"name": "관광지 추천"}], "updated": "2026-11-25T10:00:00.000+0900",
    "description": {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "공공데이터 API 검토"}]}]},
    "subtasks": [{"fields": {"status": {"statusCategory": {"key": "done"}}}}],
    "issuelinks": [{"outwardIssue": {"key": "TRIP-101"}}],
    "timetracking": {"originalEstimateSeconds": 57600, "remainingEstimateSeconds": 36000, "timeSpentSeconds": 21600},
    "customfield_10020": [{"name": "Sprint 11", "state": "active"}],
    "comment": {"comments": [{"body": {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": f"코멘트 {n}"}]}]}} for n in range(5)]},
}}


def jira(handler, projects=("TRIP",)):
    return JiraCloudSource(base_url="https://example.atlassian.net", email="bot@example.com", api_token="token",
                           projects=projects, transport=httpx.MockTransport(handler))


def test_search_and_detail_are_parsed_from_jira_responses():
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.path == "/rest/api/3/search/jql":
            return httpx.Response(200, json={"issues": [ISSUE]})
        return httpx.Response(200, json=ISSUE)

    source = jira(handler)
    found = source.search_jira_issues("맛집")
    assert json.loads(seen[0].content)["jql"].startswith("project in (TRIP) AND summary ~ \"맛집\"")
    assert seen[0].headers["authorization"].startswith("Basic ")
    assert (found[0].key, found[0].status_category, found[0].parent_key) == ("TRIP-118", "in_progress", "TRIP-100")
    assert found[0].url == "https://example.atlassian.net/browse/TRIP-118"
    detail = source.get_jira_issue("TRIP-118")
    assert detail.description == "공공데이터 API 검토"
    assert (detail.original_estimate_hours, detail.remaining_estimate_hours, detail.time_spent_hours) == (16, 10, 6)
    assert (detail.sprint, detail.sprint_state, detail.linked_keys) == ("Sprint 11", "active", ["TRIP-101"])
    assert detail.subtasks_by_status == {"done": 1}
    assert detail.recent_comments == ["코멘트 2", "코멘트 3", "코멘트 4"]


def test_other_project_is_refused_without_calling_jira():
    calls = []
    source = jira(lambda r: calls.append(r) or httpx.Response(200, json=ISSUE))
    with pytest.raises(WorkActivityError) as err:
        source.get_jira_issue("HR-1")
    assert err.value.code == "not_allowed" and not calls


@pytest.mark.parametrize("status, code", [(401, "unavailable"), (403, "unavailable"), (404, "not_found"), (500, "unavailable")])
def test_http_errors_map_to_safe_codes(status, code):
    with pytest.raises(WorkActivityError) as err:
        jira(lambda r: httpx.Response(status, json={})).get_jira_issue("TRIP-1")
    assert err.value.code == code


def test_jira_requires_https_credentials_and_project_keys():
    for kw in ({"base_url": "http://x.atlassian.net"}, {"api_token": ""}, {"projects": ["trip"]}):
        args = {"base_url": "https://x.atlassian.net", "email": "a@b.c", "api_token": "t", "projects": ["TRIP"], **kw}
        with pytest.raises(WorkActivityError):
            JiraCloudSource(**args)


# ---- 도구 실행기 ----
def test_tools_validate_arguments_enforce_budget_and_collect_snapshot():
    tools = WorkActivityTools(FakeWorkActivitySource(), budget=3, clock=lambda: "2026-11-27T10:00:00+00:00")
    assert [i.key for i in tools.search_jira_issues("맛집 API")] == ["TRIP-118"]
    for bad in ({"query": "  "}, {"query": "x", "status_category": "closed"}, {"query": "x", "updated_since": "11/01"}):
        with pytest.raises(WorkActivityError):
            tools.search_jira_issues(**bad)
    with pytest.raises(WorkActivityError, match="허용"):
        tools.get_jira_issue("HR-1")
    detail = tools.get_jira_issue("TRIP-118")
    assert detail.remaining_estimate_hours == 10
    tools.get_epic_progress("TRIP-100")
    with pytest.raises(WorkActivityError) as err:
        tools.search_jira_issues("일정")
    assert err.value.code == "budget"
    snap = tools.snapshot()
    assert [i.key for i in snap.issues] == ["TRIP-118"] and snap.issues[0].remaining_estimate_hours == 10  # 상세가 남는다
    assert snap.sources[0].scope == ["TRIP"]


def test_fake_source_epic_progress_and_catalog():
    source = FakeWorkActivitySource()
    progress = source.get_epic_progress("TRIP-100")
    assert progress.children_by_status == {"done": 1, "in_progress": 1} and progress.remaining_estimate_hours == 10
    catalog = source.list_project_components()
    assert "관광지 추천" in catalog.components and [e.key for e in catalog.epics] == ["TRIP-100"]


def test_factory_allows_direct_jira_only_in_lab(monkeypatch):
    settings = SimpleNamespace(work_activity_source="jira", work_activity_fixture="", jira_base_url="https://x.atlassian.net",
                               jira_email="a@b.c", jira_api_token="t", jira_projects="TRIP", jira_sprint_field="customfield_10020",
                               jira_timeout_sec=5)
    monkeypatch.delenv("PROTOTYPE_LAB", raising=False)
    with pytest.raises(WorkActivityError, match="PROTOTYPE_LAB"):
        build_work_activity_source(settings)
    monkeypatch.setenv("PROTOTYPE_LAB", "1")
    assert build_work_activity_source(settings).jira_projects == ("TRIP",)
    assert build_work_activity_source(SimpleNamespace(work_activity_source="none")) is None


# ---- 조사 단계 ----
class ScriptedLlm:
    def __init__(self, steps):
        self.steps, self.calls = list(steps), []

    def complete_structured(self, **kw):
        self.calls.append(json.loads(kw["user"]))
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return SimpleNamespace(parsed=ProbeStep(thought="조사", **step))


TARGETS = [{"number": 1, "text": "챗봇 관광지 추천 답변에 주변 맛집 정보 표시"}]


def test_probe_reads_related_issues_and_pins_them():
    llm = ScriptedLlm([{"action": "search_jira_issues", "query": "맛집"},
                       {"action": "get_jira_issue", "key": "TRIP-118"},
                       {"action": "get_jira_issue", "key": "HR-9"},          # 허용 밖 — 오류를 알리고 계속
                       {"action": "finish"}])
    snap = ActivityProbe(llm, WorkActivityTools(FakeWorkActivitySource())).run(TARGETS)
    assert [i.key for i in snap.issues] == ["TRIP-118"] and snap.issues[0].description
    assert "error" in llm.calls[-1]["history"][-1]["result"]
    assert snap.unavailable_reason is None


def test_probe_stops_with_reason_when_jira_is_unavailable():
    def down(request):
        return httpx.Response(401, json={})
    llm = ScriptedLlm([{"action": "search_jira_issues", "query": "맛집"}])
    snap = ActivityProbe(llm, WorkActivityTools(jira(down))).run(TARGETS)
    assert snap.empty and "인증" in snap.unavailable_reason


def test_probe_model_failure_keeps_only_issues_read_in_detail():
    llm = ScriptedLlm([{"action": "search_jira_issues", "query": "맛집"}, RuntimeError("timeout")])
    snap = ActivityProbe(llm, WorkActivityTools(FakeWorkActivitySource())).run(TARGETS)
    assert snap.issues == [] and snap.unavailable_reason is None  # 검색으로 보기만 한 이슈는 남기지 않는다
    llm = ScriptedLlm([{"action": "search_jira_issues", "query": "맛집"}, {"action": "get_jira_issue", "key": "TRIP-118"},
                       RuntimeError("timeout")])
    assert [i.key for i in ActivityProbe(llm, WorkActivityTools(FakeWorkActivitySource())).run(TARGETS).issues] == ["TRIP-118"]


def test_probe_keeps_only_issues_marked_relevant_at_finish():
    llm = ScriptedLlm([{"action": "search_jira_issues", "query": "추천"},   # TRIP-100·101·118 등이 함께 걸린다
                       {"action": "finish", "relevant_keys": ["TRIP-101", "TRIP-999"]}])
    snap = ActivityProbe(llm, WorkActivityTools(FakeWorkActivitySource())).run(TARGETS)
    assert [i.key for i in snap.issues] == ["TRIP-101"]  # 보지 않은 키는 무시


def test_title_search_falls_back_to_full_text_only_when_empty():
    jqls = []

    def handler(request):
        jql = json.loads(request.content)["jql"]
        jqls.append(jql.split(" AND ")[1])
        return httpx.Response(200, json={"issues": [] if jql.split(" AND ")[1].startswith("summary") else [ISSUE]})

    assert [i.key for i in jira(handler).search_jira_issues("API")] == ["TRIP-118"]
    assert jqls == ['summary ~ "API" ORDER BY updated DESC', 'text ~ "API" ORDER BY updated DESC']
    jqls.clear()
    jira(lambda r: jqls.append(1) or httpx.Response(200, json={"issues": [ISSUE]})).search_jira_issues("맛집")
    assert len(jqls) == 1  # 제목에서 찾으면 본문 검색은 하지 않는다


# ---- 총괄 연결 ----
def test_supervisor_probes_once_and_reuses_snapshot_for_drafting(setup, monkeypatch):
    from app.integrations.work_activity.models import WorkActivitySnapshot
    from app.orchestration.run_supervisor import RunSupervisorRuntime
    from test_run_supervisor import runtime
    snap = WorkActivitySnapshot.build(fetched_at="t", issues=[FakeWorkActivitySource().issues["TRIP-118"]])
    probes, inputs = [], []
    monkeypatch.setattr(RunSupervisorRuntime, "_probe_activity", lambda self, current: probes.append(1) or snap)
    agent3 = setup[1].agent3
    original = agent3.run
    agent3.run = lambda inp: inputs.append(inp) or original(inp)
    results = [runtime(setup).step("run-1") for _ in range(6)]
    assert results[-1]["status"] == "waiting_final_review"  # 같은 스냅샷이라 초안 단계에서 입력 변경 오류가 없다
    assert len(probes) == 1
    assert inputs[0].work_activity.snapshot_hash == snap.snapshot_hash
    run = setup[0].get_coordinated_run("run-1")
    payload = setup[0].get_result("run-1", run.state.agents["agent3"].current_result_id).result_payload
    assert payload["source"]["activity_snapshot_hash"] == snap.snapshot_hash
    assert payload["work_activity"]["snapshot_hash"] == snap.snapshot_hash


def test_gateway_url_uses_site_address_for_issue_links():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path.endswith("/rest/api/3/serverInfo"):
            return httpx.Response(200, json={"baseUrl": "https://team.atlassian.net"})
        return httpx.Response(200, json={"issues": [ISSUE]})

    source = JiraCloudSource(base_url="https://api.atlassian.com/ex/jira/cloud-1", email="a@b.c", api_token="t",
                             projects=["TRIP"], transport=httpx.MockTransport(handler))
    found = source.search_jira_issues("맛집") + source.search_jira_issues("카드")
    assert found[0].url == "https://team.atlassian.net/browse/TRIP-118"
    assert calls.count("/ex/jira/cloud-1/rest/api/3/serverInfo") == 1  # 사이트 주소는 한 번만 조회
    explicit = JiraCloudSource(base_url="https://api.atlassian.com/ex/jira/cloud-1", email="a@b.c", api_token="t",
                               projects=["TRIP"], site_url="https://given.atlassian.net/",
                               transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"issues": [ISSUE]})))
    assert explicit.search_jira_issues("맛집")[0].url == "https://given.atlassian.net/browse/TRIP-118"
