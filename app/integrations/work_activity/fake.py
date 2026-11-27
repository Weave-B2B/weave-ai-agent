"""합성 진행 자료 어댑터 (WORK_ACTIVITY_SOURCE=fake). 외부 호출 없이 fixture JSON으로 조회한다.

fixture는 실제 Jira·GitHub 자료가 아니다. 개발 화면과 테스트에서 조사 단계·Clarifier 흐름을 확인하는 데 쓴다.
"""
import json
from pathlib import Path

from app.integrations.work_activity.base import (
    EpicProgress, ProjectCatalog, WorkActivityError, WorkActivitySource, project_of,
)
from app.integrations.work_activity.models import Commit, JiraIssue, PullRequest

DEFAULT_FIXTURE = Path(__file__).resolve().parents[3] / "data" / "work_activity" / "tripbranch-synthetic.json"


def _tokens(query: str) -> list[str]:
    return [t.casefold() for t in query.split() if len(t) >= 2]


def _matches(query: str, *texts) -> bool:
    haystack = " ".join(str(t or "") for t in texts).casefold()
    return any(token in haystack for token in _tokens(query))


class FakeWorkActivitySource(WorkActivitySource):
    def __init__(self, fixture: Path | str | dict = DEFAULT_FIXTURE):
        data = fixture if isinstance(fixture, dict) else json.loads(Path(fixture).read_text(encoding="utf-8"))
        self.jira_projects = tuple(data.get("jira_projects", []))
        self.github_repos = tuple(data.get("github_repos", []))
        self.components = list(data.get("components", []))
        self.issues = {i["key"]: JiraIssue.model_validate(i) for i in data.get("issues", [])}
        self.pull_requests = [PullRequest.model_validate(p) for p in data.get("pull_requests", [])]
        self.commits = [Commit.model_validate(c) for c in data.get("commits", [])]

    @staticmethod
    def _summary(issue: JiraIssue) -> JiraIssue:
        """검색 결과는 요약 필드만 (상세는 get_jira_issue로)."""
        return issue.model_copy(update={"description": None, "subtasks_by_status": {}, "linked_keys": [], "sprint": None,
                                        "sprint_state": None, "original_estimate_hours": None,
                                        "remaining_estimate_hours": None, "time_spent_hours": None, "recent_comments": []})

    def search_jira_issues(self, query, *, status_category=None, updated_since=None, limit=10):
        def search(*fields):
            return [i for i in self.issues.values()
                    if _matches(query, *(f(i) for f in fields))
                    and (status_category is None or i.status_category == status_category)
                    and (updated_since is None or (i.updated_at or "") >= updated_since)]
        # Jira Cloud 어댑터와 같이 제목 먼저, 없으면 설명·컴포넌트·라벨까지
        found = search(lambda i: i.title) or search(lambda i: i.title, lambda i: i.description,
                                                    lambda i: " ".join(i.components), lambda i: " ".join(i.labels))
        found.sort(key=lambda i: i.updated_at or "", reverse=True)
        return [self._summary(i) for i in found[:limit]]

    def get_jira_issue(self, key):
        if project_of(key) not in self.jira_projects:
            raise WorkActivityError("not_allowed", f"{key}는 조회를 허용한 Jira 프로젝트가 아닙니다.")
        if key not in self.issues:
            raise WorkActivityError("not_found", f"{key} 이슈가 없습니다.")
        issue = self.issues[key]
        children = [i for i in self.issues.values() if i.parent_key == key]
        by_status = {}
        for child in children:
            by_status[child.status_category] = by_status.get(child.status_category, 0) + 1
        return issue.model_copy(update={"subtasks_by_status": by_status or issue.subtasks_by_status})

    def get_epic_progress(self, epic_key):
        epic = self.get_jira_issue(epic_key)
        children = [i for i in self.issues.values() if i.parent_key == epic_key]
        by_status = {}
        for child in children:
            by_status[child.status_category] = by_status.get(child.status_category, 0) + 1
        remaining = [c.remaining_estimate_hours for c in children if c.remaining_estimate_hours is not None]
        return EpicProgress(epic_key=epic_key, title=epic.title, children_by_status=by_status,
                            remaining_estimate_hours=sum(remaining) if remaining else None,
                            child_keys=sorted(c.key for c in children))

    def list_project_components(self):
        epics = [self._summary(i) for i in self.issues.values() if (i.issue_type or "").casefold() == "epic"]
        return ProjectCatalog(components=self.components, epics=epics)
