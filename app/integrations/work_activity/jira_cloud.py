"""Jira Cloud 직접 조회 어댑터 (REST API v3, 읽기 전용 API 토큰).

검증 단계에서 개발자 화면(lab)에서만 쓴다. 운영은 Backend 대리 조회 어댑터로 바꾼다(같은 인터페이스).
JQL은 서버가 만든다: 허용 프로젝트 목록 + 이스케이프한 검색어. 모델이 쓴 JQL은 받지 않는다.
"""
import re

import httpx

from app.integrations.work_activity.base import (
    EpicProgress, ProjectCatalog, WorkActivityError, WorkActivitySource, clean_query, project_of,
)
from app.integrations.work_activity.models import JiraIssue

_PROJECT = re.compile(r"^[A-Z][A-Z0-9]{1,9}$")
_JQL_SPECIAL = re.compile(r'[+\-&|!(){}\[\]^~*?\\:/"\']')
_STATUS_CATEGORY = {"new": "todo", "indeterminate": "in_progress", "done": "done"}
_STATUS_JQL = {"todo": "To Do", "in_progress": "In Progress", "done": "Done"}
SUMMARY_FIELDS = ["summary", "status", "issuetype", "assignee", "parent", "labels", "components", "updated"]
DETAIL_FIELDS = SUMMARY_FIELDS + ["description", "subtasks", "issuelinks", "timetracking", "comment"]


def escape_text(query: str) -> str:
    """JQL text ~ 검색어: 문법 문자를 공백으로 바꾸고 따옴표로 감싼다."""
    text = " ".join(_JQL_SPECIAL.sub(" ", clean_query(query)).split())
    if not text:
        raise WorkActivityError("invalid", "검색어에 검색할 단어가 없습니다.")
    return f'"{text}"'


def build_search_jql(query: str, projects, *, status_category=None, updated_since=None,
                     field: str = "summary") -> str:
    """field=summary는 제목만, text는 제목·설명·코멘트 전체에서 찾는다."""
    projects = sorted(projects)
    if not projects or not all(_PROJECT.match(p) for p in projects):
        raise WorkActivityError("not_allowed", "조회를 허용한 Jira 프로젝트가 없습니다.")
    if field not in ("summary", "text"):
        raise WorkActivityError("invalid", "검색 필드는 summary 또는 text입니다.")
    clauses = [f"project in ({', '.join(projects)})", f"{field} ~ {escape_text(query)}"]
    if status_category:
        clauses.append(f'statusCategory = "{_STATUS_JQL[status_category]}"')
    if updated_since:
        clauses.append(f'updated >= "{updated_since}"')
    return " AND ".join(clauses) + " ORDER BY updated DESC"


def adf_to_text(node) -> str:
    """Atlassian Document Format → 일반 텍스트 (문단은 줄바꿈)."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(adf_to_text(n) for n in node)
    text = node.get("text", "") if node.get("type") == "text" else adf_to_text(node.get("content"))
    return text + ("\n" if node.get("type") in ("paragraph", "heading", "listItem") else "")


def _hours(seconds) -> float | None:
    return round(seconds / 3600, 2) if isinstance(seconds, (int, float)) and seconds >= 0 else None


class JiraCloudSource(WorkActivitySource):
    def __init__(self, *, base_url: str, email: str, api_token: str, projects, sprint_field: str = "customfield_10020",
                 timeout: float = 10.0, transport: httpx.BaseTransport | None = None, site_url: str | None = None):
        if not base_url.startswith("https://") or not email or not api_token:
            raise WorkActivityError("unavailable", "Jira 연결 설정(주소·계정·토큰)이 필요합니다.")
        self.jira_projects = tuple(sorted({p.strip() for p in projects if p.strip()}))
        if not self.jira_projects or not all(_PROJECT.match(p) for p in self.jira_projects):
            raise WorkActivityError("unavailable", "조회할 Jira 프로젝트 키(JIRA_PROJECTS)가 필요합니다.")
        self.base_url, self.sprint_field = base_url.rstrip("/"), sprint_field
        # 범위 지정 토큰은 게이트웨이(api.atlassian.com/ex/jira/<cloudId>)로 호출한다. 사람이 여는 이슈 링크는 사이트 주소로 만든다.
        self._site_url = site_url.rstrip("/") if site_url else (None if "api.atlassian.com" in self.base_url else self.base_url)
        self._http = httpx.Client(base_url=self.base_url, auth=(email, api_token), timeout=timeout, transport=transport,
                                  headers={"Accept": "application/json"})

    # ---- HTTP ----
    def _request(self, method: str, path: str, **kw) -> dict:
        try:
            response = self._http.request(method, path, **kw)
        except httpx.HTTPError as exc:
            raise WorkActivityError("unavailable", "Jira에 연결하지 못했습니다.") from exc
        if response.status_code in (401, 403):
            raise WorkActivityError("unavailable", "Jira 인증 또는 권한을 확인해 주세요.")
        if response.status_code == 404:
            raise WorkActivityError("not_found", "Jira에서 찾지 못했습니다.")
        if response.status_code >= 400:
            raise WorkActivityError("unavailable", f"Jira 조회 실패({response.status_code}).")
        return response.json()

    def _search(self, jql: str, fields: list[str], limit: int) -> list[dict]:
        body = self._request("POST", "/rest/api/3/search/jql", json={"jql": jql, "fields": fields, "maxResults": limit})
        return body.get("issues", [])

    @property
    def site_url(self) -> str | None:
        if self._site_url is None:
            try:
                self._site_url = (self._request("GET", "/rest/api/3/serverInfo").get("baseUrl") or "").rstrip("/") or ""
            except WorkActivityError:
                self._site_url = ""  # 링크 없이 키만 남긴다
        return self._site_url or None

    # ---- 변환 ----
    def _issue(self, raw: dict, *, detail: bool = False) -> JiraIssue:
        f = raw.get("fields", {})
        status = f.get("status") or {}
        data = {
            "key": raw["key"], "title": f.get("summary") or "", "issue_type": (f.get("issuetype") or {}).get("name"),
            "status": status.get("name") or "", "status_category": _STATUS_CATEGORY.get(
                (status.get("statusCategory") or {}).get("key"), "unknown"),
            "assignee": (f.get("assignee") or {}).get("displayName"), "parent_key": (f.get("parent") or {}).get("key"),
            "labels": f.get("labels") or [], "components": [c.get("name") for c in f.get("components") or [] if c.get("name")],
            "updated_at": f.get("updated"), "url": f"{self.site_url}/browse/{raw['key']}" if self.site_url else None,
        }
        if detail:
            by_status = {}
            for sub in f.get("subtasks") or []:
                key = _STATUS_CATEGORY.get(((sub.get("fields") or {}).get("status") or {}).get("statusCategory", {}).get("key"), "unknown")
                by_status[key] = by_status.get(key, 0) + 1
            links = [(l.get("outwardIssue") or l.get("inwardIssue") or {}).get("key") for l in f.get("issuelinks") or []]
            tracking = f.get("timetracking") or {}
            sprints = f.get(self.sprint_field) or []
            sprint = sprints[-1] if isinstance(sprints, list) and sprints and isinstance(sprints[-1], dict) else {}
            comments = ((f.get("comment") or {}).get("comments") or [])[-3:]
            data.update({
                "description": adf_to_text(f.get("description")).strip() or None, "subtasks_by_status": by_status,
                "linked_keys": [k for k in links if k], "sprint": sprint.get("name"), "sprint_state": sprint.get("state"),
                "original_estimate_hours": _hours(tracking.get("originalEstimateSeconds")),
                "remaining_estimate_hours": _hours(tracking.get("remainingEstimateSeconds")),
                "time_spent_hours": _hours(tracking.get("timeSpentSeconds")),
                "recent_comments": [adf_to_text(c.get("body")).strip() for c in comments if c.get("body")],
            })
        return JiraIssue.model_validate(data)

    def _allowed(self, key: str) -> str:
        if project_of(key) not in self.jira_projects:
            raise WorkActivityError("not_allowed", f"{key}는 조회를 허용한 Jira 프로젝트가 아닙니다.")
        return key

    # ---- 인터페이스 ----
    def search_jira_issues(self, query, *, status_category=None, updated_since=None, limit=10):
        # 제목에서 먼저 찾고, 없을 때만 설명·코멘트까지 넓힌다 (흔한 단어가 본문에서 대량으로 걸리는 것을 막는다)
        for field in ("summary", "text"):
            jql = build_search_jql(query, self.jira_projects, status_category=status_category,
                                   updated_since=updated_since, field=field)
            found = self._search(jql, SUMMARY_FIELDS, limit)
            if found:
                return [self._issue(raw) for raw in found]
        return []

    def get_jira_issue(self, key):
        fields = ",".join(DETAIL_FIELDS + [self.sprint_field])
        return self._issue(self._request("GET", f"/rest/api/3/issue/{self._allowed(key)}", params={"fields": fields}), detail=True)

    def get_epic_progress(self, epic_key):
        epic = self._issue(self._request("GET", f"/rest/api/3/issue/{self._allowed(epic_key)}",
                                         params={"fields": "summary,status,issuetype"}))
        children = self._search(f"parent = {epic_key}", ["status", "timeestimate"], 50)
        by_status, remaining = {}, []
        for child in children:
            f = child.get("fields", {})
            key = _STATUS_CATEGORY.get(((f.get("status") or {}).get("statusCategory") or {}).get("key"), "unknown")
            by_status[key] = by_status.get(key, 0) + 1
            if (hours := _hours(f.get("timeestimate"))) is not None:
                remaining.append(hours)
        return EpicProgress(epic_key=epic_key, title=epic.title, children_by_status=by_status,
                            remaining_estimate_hours=round(sum(remaining), 2) if remaining else None,
                            child_keys=sorted(c["key"] for c in children))

    def list_project_components(self):
        components = []
        for project in self.jira_projects:
            for c in self._request("GET", f"/rest/api/3/project/{project}/components"):
                if c.get("name") and c["name"] not in components:
                    components.append(c["name"])
        jql = f"project in ({', '.join(self.jira_projects)}) AND issuetype = Epic AND statusCategory != Done ORDER BY updated DESC"
        epics = [self._issue(raw) for raw in self._search(jql, SUMMARY_FIELDS, 20)]
        return ProjectCatalog(components=components, epics=epics)
