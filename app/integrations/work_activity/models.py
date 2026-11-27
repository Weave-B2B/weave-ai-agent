"""작업 진행 자료 스냅샷: Jira 이슈·GitHub PR·커밋 메시지.

- 코드 본문·diff는 담지 않는다. PR은 변경 파일 '경로'까지만.
- 외부 본문(설명·코멘트·PR 본문)은 길이를 잘라 저장하고, 업무 자료로만 다룬다(지시가 아님).
- 같은 내용이면 같은 해시가 나오도록 정렬·중복 제거한다. 해시는 재산정 때 같은 자료를 썼는지 확인하는 데 쓴다.
"""
import hashlib
import json
import re
from typing import Literal

from pydantic import Field, field_validator, model_validator

from schema.schemas import EvidenceRef, _M

DESCRIPTION_CHARS = 1500
COMMENT_CHARS = 300
MAX_COMMENTS = 3
MAX_COMMIT_MESSAGES = 30
MAX_CHANGED_FILES = 100
COMMIT_MESSAGE_CHARS = 200

# 한글이 바로 붙어도(예: "TRIP-12를") 잡히도록 \b 대신 영숫자 경계만 본다
_ISSUE_KEY = re.compile(r"(?<![A-Za-z0-9])([A-Z][A-Z0-9]{1,9})-(\d+)(?![0-9])")


def extract_issue_keys(*texts: str | None, projects: set[str] | None = None) -> list[str]:
    """제목·브랜치·본문·커밋 메시지에서 Jira 이슈 키(PROJ-123)를 순서대로 뽑는다. projects가 있으면 그 프로젝트만."""
    keys = []
    for text in texts:
        for match in _ISSUE_KEY.finditer(text or ""):
            if projects is not None and match[1] not in projects:
                continue
            if match[0] not in keys:
                keys.append(match[0])
    return keys


def _clip(text: str | None, limit: int) -> str | None:
    if text is None:
        return None
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + "…"


def _first_line(text: str) -> str:
    return _clip((text or "").strip().splitlines()[0] if (text or "").strip() else "", COMMIT_MESSAGE_CHARS) or ""


class JiraIssue(_M):
    key: str = Field(pattern=r"^[A-Z][A-Z0-9]{1,9}-\d+$")
    title: str
    issue_type: str | None = None
    status: str
    status_category: Literal["todo", "in_progress", "done", "unknown"] = "unknown"
    assignee: str | None = None
    parent_key: str | None = Field(None, description="상위 이슈(에픽) 키")
    labels: list[str] = Field(default_factory=list)
    components: list[str] = Field(default_factory=list)
    updated_at: str | None = None
    url: str | None = None
    # get_jira_issue로 읽었을 때만 채운다
    description: str | None = None
    subtasks_by_status: dict[str, int] = Field(default_factory=dict)
    linked_keys: list[str] = Field(default_factory=list)
    sprint: str | None = None
    sprint_state: str | None = None
    original_estimate_hours: float | None = Field(None, ge=0)
    remaining_estimate_hours: float | None = Field(None, ge=0)
    time_spent_hours: float | None = Field(None, ge=0)
    recent_comments: list[str] = Field(default_factory=list)

    @field_validator("description")
    @classmethod
    def _limit_description(cls, value):
        return _clip(value, DESCRIPTION_CHARS)

    @field_validator("recent_comments")
    @classmethod
    def _limit_comments(cls, value):
        return [_clip(c, COMMENT_CHARS) for c in value[:MAX_COMMENTS]]


class PullRequest(_M):
    repo: str = Field(pattern=r"^[\w.-]+/[\w.-]+$")
    number: int = Field(gt=0)
    title: str
    state: Literal["open", "closed", "merged"]
    merged_at: str | None = None
    author: str | None = None
    head_branch: str | None = None
    base_branch: str | None = None
    labels: list[str] = Field(default_factory=list)
    issue_keys: list[str] = Field(default_factory=list)
    url: str | None = None
    # get_pull_request로 읽었을 때만 채운다. 코드 본문·diff는 담지 않는다.
    body: str | None = None
    changed_files: list[str] = Field(default_factory=list, description="변경 파일 경로만")
    additions: int | None = Field(None, ge=0)
    deletions: int | None = Field(None, ge=0)
    changed_file_count: int | None = Field(None, ge=0)
    commit_messages: list[str] = Field(default_factory=list, description="커밋 메시지 첫 줄")
    review_state: str | None = None

    @property
    def ref(self) -> str:
        return f"{self.repo}#{self.number}"

    @field_validator("body")
    @classmethod
    def _limit_body(cls, value):
        return _clip(value, DESCRIPTION_CHARS)

    @field_validator("changed_files")
    @classmethod
    def _limit_files(cls, value):
        return value[:MAX_CHANGED_FILES]

    @field_validator("commit_messages")
    @classmethod
    def _limit_messages(cls, value):
        return [_first_line(m) for m in value[:MAX_COMMIT_MESSAGES]]


class Commit(_M):
    repo: str = Field(pattern=r"^[\w.-]+/[\w.-]+$")
    sha: str = Field(pattern=r"^[0-9a-f]{7,40}$")
    message: str = Field(description="커밋 메시지 첫 줄")
    author: str | None = None
    committed_at: str | None = None
    issue_keys: list[str] = Field(default_factory=list)
    url: str | None = None

    @property
    def ref(self) -> str:
        return f"{self.repo}@{self.sha[:12]}"

    @field_validator("message")
    @classmethod
    def _limit_message(cls, value):
        return _first_line(value)


class ActivitySource(_M):
    system: Literal["jira", "github"]
    scope: list[str] = Field(default_factory=list, description="조회를 허용한 Jira 프로젝트 키·GitHub 저장소")


class WorkActivitySnapshot(_M):
    """한 번의 산정에 쓴 진행 자료 묶음. 해시가 같으면 같은 자료다(조회 시각은 해시에 넣지 않는다)."""
    sources: list[ActivitySource] = Field(default_factory=list)
    fetched_at: str
    issues: list[JiraIssue] = Field(default_factory=list)
    pull_requests: list[PullRequest] = Field(default_factory=list)
    commits: list[Commit] = Field(default_factory=list)
    unavailable_reason: str | None = Field(None, description="조회하지 못했을 때의 사유 (자료 없이 산정)")
    snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @staticmethod
    def _content(sources, issues, pull_requests, commits, unavailable_reason) -> dict:
        dump = lambda rows: [r.model_dump(mode="json") for r in rows]
        return {"sources": dump(sorted(sources, key=lambda s: (s.system, s.scope))),
                "issues": dump(sorted(issues, key=lambda i: i.key)),
                "pull_requests": dump(sorted(pull_requests, key=lambda p: (p.repo, p.number))),
                "commits": dump(sorted(commits, key=lambda c: (c.repo, c.sha))),
                "unavailable_reason": unavailable_reason}

    @classmethod
    def compute_hash(cls, **parts) -> str:
        text = json.dumps(cls._content(**parts), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(text.encode()).hexdigest()

    @classmethod
    def build(cls, *, fetched_at: str, sources=(), issues=(), pull_requests=(), commits=(),
              unavailable_reason: str | None = None) -> "WorkActivitySnapshot":
        """정렬·중복 제거 후 해시를 붙여 만든다. 같은 키는 자세히 읽은 쪽(필드가 더 많은 쪽)을 남긴다."""
        def unique(rows, key):
            kept = {}
            for row in rows:
                k = key(row)
                if k not in kept or len(row.model_dump(exclude_defaults=True)) > len(kept[k].model_dump(exclude_defaults=True)):
                    kept[k] = row
            return sorted(kept.values(), key=key)
        parts = {"sources": list(sources), "issues": unique(issues, lambda i: i.key),
                 "pull_requests": unique(pull_requests, lambda p: (p.repo, p.number)),
                 "commits": unique(commits, lambda c: (c.repo, c.sha)), "unavailable_reason": unavailable_reason}
        return cls(fetched_at=fetched_at, snapshot_hash=cls.compute_hash(**parts), **parts)

    @model_validator(mode="after")
    def _hash_matches(self):
        expected = self.compute_hash(sources=self.sources, issues=self.issues, pull_requests=self.pull_requests,
                                     commits=self.commits, unavailable_reason=self.unavailable_reason)
        if self.snapshot_hash != expected:
            raise ValueError("snapshot_hash does not match the snapshot content")
        return self

    @property
    def empty(self) -> bool:
        return not (self.issues or self.pull_requests or self.commits)

    def evidence_refs(self) -> list[EvidenceRef]:
        """진행 자료 근거 — 계약 근거가 아니다(source_type=activity)."""
        common = {"source_type": "activity", "fetched_at": self.fetched_at, "snapshot_hash": self.snapshot_hash}
        return ([EvidenceRef(system="jira", ref=i.key, title=i.title, url=i.url, **common) for i in self.issues]
                + [EvidenceRef(system="github", ref=p.ref, title=p.title, url=p.url, **common) for p in self.pull_requests]
                + [EvidenceRef(system="github", ref=c.ref, title=c.message, url=c.url, **common) for c in self.commits])

    def prompt_view(self) -> dict:
        """산정 모델에 보낼 요약. 저장본보다 짧게 줄인다."""
        return {
            "fetched_at": self.fetched_at,
            "issues": [{k: v for k, v in {
                "key": i.key, "title": i.title, "type": i.issue_type, "status": i.status, "status_category": i.status_category,
                "assignee": i.assignee, "parent": i.parent_key, "sprint": i.sprint,
                "remaining_hours": i.remaining_estimate_hours, "spent_hours": i.time_spent_hours,
                "subtasks_by_status": i.subtasks_by_status or None, "linked": i.linked_keys or None,
                "description": _clip(i.description, 500)}.items() if v not in (None, [], {})} for i in self.issues],
            "pull_requests": [{k: v for k, v in {
                "ref": p.ref, "title": p.title, "state": p.state, "merged_at": p.merged_at, "issue_keys": p.issue_keys or None,
                "changed_files": p.changed_files[:20] or None, "commit_messages": p.commit_messages[:10] or None}.items()
                if v not in (None, [], {})} for p in self.pull_requests],
            "commits": [{"ref": c.ref, "message": c.message, "issue_keys": c.issue_keys or None} for c in self.commits],
            **({"unavailable_reason": self.unavailable_reason} if self.unavailable_reason else {}),
        }
