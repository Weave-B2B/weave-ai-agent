"""설정에 따라 진행 자료 조회 어댑터를 만든다.

- none: 조회하지 않는다(기본).
- fake: 합성 데이터 fixture (외부 호출 없음).
- jira: Jira Cloud 직접 호출. 검증 단계라 개발자 화면(PROTOTYPE_LAB=1)에서만 허용한다.
"""
import os

from app.integrations.work_activity.base import WorkActivityError, WorkActivitySource


def build_work_activity_source(settings) -> WorkActivitySource | None:
    kind = (settings.work_activity_source or "none").strip().casefold()
    if kind in ("", "none"):
        return None
    if kind == "fake":
        from app.integrations.work_activity.fake import DEFAULT_FIXTURE, FakeWorkActivitySource
        return FakeWorkActivitySource(settings.work_activity_fixture or DEFAULT_FIXTURE)
    if kind == "jira":
        if os.environ.get("PROTOTYPE_LAB") != "1":
            raise WorkActivityError("unavailable", "Jira 직접 조회는 개발자 화면(PROTOTYPE_LAB=1)에서만 쓸 수 있습니다.")
        from app.integrations.work_activity.jira_cloud import JiraCloudSource
        return JiraCloudSource(base_url=settings.jira_base_url, email=settings.jira_email,
                               api_token=settings.jira_api_token, projects=settings.jira_projects.split(","),
                               sprint_field=settings.jira_sprint_field, timeout=settings.jira_timeout_sec,
                               site_url=getattr(settings, "jira_site_url", "") or None)
    raise WorkActivityError("unavailable", f"알 수 없는 진행 자료 조회 방식입니다: {kind}")
