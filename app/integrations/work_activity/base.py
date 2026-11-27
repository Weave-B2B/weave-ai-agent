"""작업 진행 자료 조회 인터페이스와 도구 실행기.

어댑터(가짜·Jira Cloud 직접·Backend 대리)는 같은 인터페이스를 구현한다. 도구 실행기는 어댑터 앞에서
인자 검증·허용 범위·호출 상한을 강제하고, 읽은 자료를 모아 스냅샷으로 고정한다. 모델이 쓴 쿼리 언어(JQL)는 받지 않는다.
"""
import re
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Literal

from pydantic import Field

from app.integrations.work_activity.models import (
    ActivitySource, Commit, JiraIssue, PullRequest, WorkActivitySnapshot,
)
from schema.schemas import _M

StatusCategory = Literal["todo", "in_progress", "done"]
SEARCH_LIMIT = 10
QUERY_CHARS = 200
DEFAULT_CALL_BUDGET = 20
_ISSUE_KEY = re.compile(r"^([A-Z][A-Z0-9]{1,9})-\d+$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class WorkActivityError(Exception):
    """조회 실패. code: unavailable(연결·인증 실패), not_allowed(허용 범위 밖), not_found, invalid(인자 오류), budget(상한)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


class EpicProgress(_M):
    epic_key: str
    title: str | None = None
    children_by_status: dict[str, int] = Field(default_factory=dict, description="todo·in_progress·done별 하위 이슈 수")
    remaining_estimate_hours: float | None = None
    child_keys: list[str] = Field(default_factory=list)


class ProjectCatalog(_M):
    """Clarifier 선택지 후보로 쓰는 이름 목록 (어떤 화면·기능인가)."""
    components: list[str] = Field(default_factory=list)
    epics: list[JiraIssue] = Field(default_factory=list)


class WorkActivitySource(ABC):
    """조회 어댑터. 모든 메서드는 읽기 전용이고, 허용 범위 검사는 어댑터와 도구 실행기 양쪽에서 한다."""
    jira_projects: tuple[str, ...] = ()
    github_repos: tuple[str, ...] = ()

    @abstractmethod
    def search_jira_issues(self, query: str, *, status_category: StatusCategory | None = None,
                           updated_since: str | None = None, limit: int = SEARCH_LIMIT) -> list[JiraIssue]: ...

    @abstractmethod
    def get_jira_issue(self, key: str) -> JiraIssue: ...

    @abstractmethod
    def get_epic_progress(self, epic_key: str) -> EpicProgress: ...

    @abstractmethod
    def list_project_components(self) -> ProjectCatalog: ...

    def search_pull_requests(self, query: str, **kw) -> list[PullRequest]:
        raise WorkActivityError("unavailable", "GitHub 조회가 연결되지 않았습니다.")


def project_of(key: str) -> str:
    match = _ISSUE_KEY.match(key or "")
    if not match:
        raise WorkActivityError("invalid", f"이슈 키 형식이 아닙니다: {key}")
    return match[1]


def clean_query(query: str) -> str:
    """검색어: 앞뒤 공백·제어문자 제거, 길이 제한. 쿼리 문법은 어댑터가 이스케이프한다."""
    text = " ".join("".join(ch if ch.isprintable() else " " for ch in (query or "")).split())
    if not text:
        raise WorkActivityError("invalid", "검색어가 필요합니다.")
    return text[:QUERY_CHARS]


class WorkActivityTools:
    """조사 단계가 부르는 도구. 인자 검증·허용 범위·호출 상한을 강제하고, 읽은 자료를 모은다."""

    def __init__(self, source: WorkActivitySource, *, budget: int = DEFAULT_CALL_BUDGET, clock=None):
        self.source, self.budget, self.calls = source, budget, 0
        self._clock = clock or (lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
        self._issues: list[JiraIssue] = []                 # 스냅샷에 남길 이슈: 상세 조회했거나 관련 있다고 고른 것
        self._seen: dict[str, JiraIssue] = {}              # 검색 결과로 본 이슈 (모델에게만 보여준다)
        self._pull_requests: list[PullRequest] = []
        self._commits: list[Commit] = []
        self.errors: list[str] = []

    def _spend(self):
        if self.calls >= self.budget:
            raise WorkActivityError("budget", f"조회는 요청당 {self.budget}회까지입니다.")
        self.calls += 1

    def _allowed_key(self, key: str) -> str:
        if project_of(key) not in self.source.jira_projects:
            raise WorkActivityError("not_allowed", f"{key}는 조회를 허용한 Jira 프로젝트가 아닙니다.")
        return key

    # ---- Jira ----
    def search_jira_issues(self, query: str, status_category: str | None = None,
                           updated_since: str | None = None, limit: int = SEARCH_LIMIT) -> list[JiraIssue]:
        query = clean_query(query)
        if status_category not in (None, "todo", "in_progress", "done"):
            raise WorkActivityError("invalid", "status_category는 todo·in_progress·done 중 하나입니다.")
        if updated_since is not None and not _DATE.match(updated_since):
            raise WorkActivityError("invalid", "updated_since는 YYYY-MM-DD 형식입니다.")
        self._spend()
        found = self.source.search_jira_issues(query, status_category=status_category, updated_since=updated_since,
                                               limit=max(1, min(int(limit or SEARCH_LIMIT), SEARCH_LIMIT)))
        found = [i for i in found if project_of(i.key) in self.source.jira_projects][:SEARCH_LIMIT]
        for issue in found:
            self._seen.setdefault(issue.key, issue)
        return found

    def get_jira_issue(self, key: str) -> JiraIssue:
        self._allowed_key(key)
        self._spend()
        found = self.source.get_jira_issue(key)
        self._issues.append(found)
        return found

    def get_epic_progress(self, epic_key: str) -> EpicProgress:
        self._allowed_key(epic_key)
        self._spend()
        return self.source.get_epic_progress(epic_key)

    def list_project_components(self) -> ProjectCatalog:
        self._spend()
        catalog = self.source.list_project_components()
        return catalog.model_copy(update={"epics": [e for e in catalog.epics if project_of(e.key) in self.source.jira_projects]})

    def keep(self, keys) -> list[str]:
        """검색으로 본 이슈 중 관련 있다고 고른 것을 스냅샷에 남긴다. 보지 않은 키는 무시한다."""
        kept = []
        for key in keys or []:
            if key in self._seen and key not in {i.key for i in self._issues}:
                self._issues.append(self._seen[key])
                kept.append(key)
        return kept

    # ---- 스냅샷 ----
    def snapshot(self, unavailable_reason: str | None = None) -> WorkActivitySnapshot:
        sources = []
        if self.source.jira_projects:
            sources.append(ActivitySource(system="jira", scope=sorted(self.source.jira_projects)))
        if self.source.github_repos:
            sources.append(ActivitySource(system="github", scope=sorted(self.source.github_repos)))
        return WorkActivitySnapshot.build(fetched_at=self._clock(), sources=sources, issues=self._issues,
                                          pull_requests=self._pull_requests, commits=self._commits,
                                          unavailable_reason=unavailable_reason)
