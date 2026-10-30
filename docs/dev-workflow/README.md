# 팀 공용 개발 워크플로

다른 제품 에이전트 개발, 기존 기능 추가, API·화면 연결에 재사용하는 저장소 공용 구성이다.
제품 자체의 `app/orchestration/` 실행 흐름과는 별개다.

**처음 사용하는 동료는 [팀 사용법](../../.agents/skills/manager-dev-workflow/references/team-usage.md)을 읽는다.**
호출 예시, 역할 선택, 착수 승인과 QA, 다른 프로젝트 설치 방법이 들어 있다.
작성자의 개인 스킬 폴더나 개인 절대 경로는 사용하지 않는다.
현재 변경은 팀 저장소에 커밋·PR로 공유한 뒤 동료의 체크아웃에서도 사용할 수 있다.

## 이 저장소에서 시작하기

```text
$manager-dev-workflow
Agent 2의 [추가할 기능]을 구현하고 싶어.
기획은 [문서 경로/링크]에 있어. 부족한 계약만 먼저 확인해줘.
대상 에이전트와 관련 API·검사만 범위로 잡고 필요한 담당자만 제안해줘.
각 담당자는 내 답변 후 시작해줘.
```

화면까지 연결하려면 사용할 메뉴, 실제 API의 입력·응답·오류 계약과 mock 사용 범위를
함께 지정한다. 현재 화면에 다른 에이전트를 연결할 때도 Agent 3의 산정·승인 절차를
그대로 적용하지 않고 **해당 에이전트의 계약과 상태**에 맞춰 연결한다.
이 예시는 사용법이며 실제 Agent 2 개발을 시작하는 지시가 아니다.

| 구성 | 위치 |
|---|---|
| 공유 스킬 | [.agents/skills/manager-dev-workflow/SKILL.md](../../.agents/skills/manager-dev-workflow/SKILL.md) |
| 실제 역할 정의 | [.codex/agents/](../../.codex/agents/) — 템플릿 원본은 스킬의 `assets/roles/` |
| 팀 기본 프로필 | [project.json](project.json) — 에이전트 1·2·3 및 기능별로 작업 범위 선택 |
| 특정 작업 프로필 | [profiles/agent3-prototype.json](profiles/agent3-prototype.json) — 기존 Agent 3 화면 작업 설정 보존 |
| 실행 기록 | `docs/dev-workflow/runs/` — 작업 실행 시 생성하며 새 작업의 승인·범위로 자동 사용하지 않음 |

## 기존 Agent 3 화면 작업을 이어갈 때

기존 프로젝트 프로필은 `profiles/agent3-prototype.json`에 보존했다.
해당 화면 작업에만 아래처럼 명시적으로 선택한다. 과거 미결 질문과 진행 상태는
실제 코드 및 [현재 실행 안내](../prototype/run.md)와 대조하고 예전 기록을 최신 상태로 단정하지 않는다.

```text
$manager-dev-workflow
작업 프로필 docs/dev-workflow/profiles/agent3-prototype.json을 사용해서
기존 Agent 3 화면 작업의 현재 상태와 다음 필요한 작업을 확인해줘.
```

Agent 3의 실제 산정·PM 승인·협의안 계약은 [MVP 문서](../agent3/agent3-mvp.md)를 따른다.
이 계약은 다른 에이전트의 기본 계약이 아니다. 새 API를 개발한 것만으로 화면이
자동 연결되는 것은 아니며 해당 화면 adapter·이벤트·상태와 관련 QA를 별도 작업으로 잡는다.
