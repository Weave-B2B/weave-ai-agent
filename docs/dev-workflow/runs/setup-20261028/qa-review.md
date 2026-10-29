# 워크플로 독립 QA — 초기 검토

- 확인일: 2026-10-28 / Asia/Seoul
- 담당: `/root/workflow_qa`
- 승인: 사용자 직접 답변 **“Figma 조사와 워크플로 QA 모두 착수 승인”**.
- 범위: 워크플로 구성·설치·승인 대기·인수인계·QA routing. 제품 코드·화면 구현·모델/API 실행 제외.
- 초기 결론: **changes_requested**. 설치 동작은 통과했고, 재사용 역할 템플릿의 프로젝트 종속 문구 1건을 수정 요청했다.

## QA-001 — 재사용 도메인 역할에 현재 프로젝트 Agent 3 문구가 포함됨

- 우선순위: P2
- 담당: 워크플로 설정 담당
- 상태: open — 별도 수정·재검증 착수 승인 대기.
- 위치: `.agents/skills/manager-dev-workflow/assets/roles/workflow-domain.toml` 및 설치본 `.codex/agents/workflow-domain.toml`.
- 재현: 실제 스킬을 빈 다른 프로젝트에 bootstrap 설치하고 도메인 역할 파일을 읽는다.
- 실제 결과: `Use the profile's assigned product agent; in the current project this is Agent 3, not a new development worker runtime.`가 새 프로젝트에도 그대로 남는다.
- 기대 결과: 공통 역할은 대상 프로필만 참조하고, Weave의 Agent 3 담당 정보는 현재 프로젝트 문서·프로필에서만 정한다.
- 영향: 다른 product agent를 만드는 새 프로젝트에서도 Agent 3라는 기존 프로젝트 의미가 역할 지시에 섞인다. 현재 Weave 작업의 기능 오류는 아니다.
- 권고: 공통 템플릿·설치본에서 프로젝트 특수 절을 제거하고 현재 프로젝트 설명에서만 유지한다.

## 실제 실행한 검증

1. `python3 -m unittest discover -s .agents/skills/manager-dev-workflow/scripts/tests -v` → **11 tests passed**.
2. 실제 bundle로 독립 임시 프로젝트 설치:
   - `--dry-run`: **22개 파일 계획**, 대상 파일 변경 0.
   - 최초 설치: **22개 파일 설치**.
   - 재실행: **0개 변경**.
   - 기존 AGENTS 원문 prefix·별도 sentinel 보존.
   - 6개 역할 TOML 파싱 및 `name == 파일 stem` 확인.
   - 새 프로필의 project_name은 null, Figma·Notion 목록은 빈 값.
   - generic bundle에 현재 Figma file key·Notion page ID가 복사되지 않음.
   - 새 프로젝트의 사용자 정의 프로필을 작성한 후 재실행해 내용 보존 확인.
   - 다른 내용의 기존 QA 역할 파일이 있으면 비정상 종료하고 대상의 추가 변경 없음.
3. 초기 요청·필수 API 미확정·QA changes_requested의 **독립 dry scenario 3개**를 작성하고 skill/protocol과 대조.

임시 검증 자료: `/private/tmp/weave-workflow-independent-qa-9xo_j0ha/`.
`checks.json`, `forward-scenarios.json`, `forward-scenarios.md`, `reviewed-sha256.json`에 결과와 검토한 아티팩트 지문이 있다.

## 독립 forward scenario의 다음 동작

### 자료 조사만 승인된 상태

매니저 메시지:

> [자료조사 담당 / source-01] 승인된 변경요청 메뉴 자료 조사를 시작합니다. 기존 화면설계와 API 계약 근거를 읽고 화면 목록·필수 상태·미확정 연결점을 전달하겠습니다. 화면 코딩이나 제품 API 실행은 하지 않습니다.

`source-01=ready`만 위임 가능하다. `ui-01=waiting_user`, `qa-01=planned`이며 실제 새 worker를 생성하지 않았다.

### 자료를 받았지만 필수 API가 모호한 상태

매니저 메시지:

> 자료조사 결과에서 화면 목록은 확인했지만 필수 API 응답 필드가 미확정입니다. 확정 URI·응답 형식을 확인할 자료를 알려주세요. 추가 조사가 필요하면 [자료조사 담당 / source-02]에 해당 계약만 확인하도록 맡겨도 될까요? 화면 구현은 이 입력과 별도 착수 승인 전까지 대기합니다.

`ui-01=waiting_input`, 새 조사 `source-02=waiting_user`. 기존 조사 task가 아직 활성 상태이고 승인 범위가 해당 질문을 포함한다면 그 task 안에서 질의할 수 있다. 완료된 조사 재착수 또는 범위 확장은 새 승인 대상이다. 필드를 임의로 만들거나 UI가 mock 성공을 실제 연동으로 보고하면 안 된다.

### QA가 changes_requested를 반환한 상태

매니저 메시지:

> QA가 승인 전 협의안 표시 오류를 확인했습니다. [화면 담당 / fix-01]은 mock/API 상태 표시만 수정하고, [QA 담당 / recheck-01]은 같은 조건으로 재검증할 예정입니다. 각 작업을 시작해도 될까요? 이번 수정과 재검증 1회를 함께 승인할 수도 있습니다.

수정과 재검증 모두 `waiting_user`. QA가 제품 코드를 직접 수정하지 않는다. 명시적으로 승인한 반복 범위 안에서만 수행하고, 두 번 실패 후 `needs_attention`으로 보고한다. 제품 PM 승인을 개발팀 승인으로 자동 생성하지 않는다. 이 내용은 사고실험 결과이며 실제 수정 루프 실행 결과가 아니다.

## 추가 아티팩트 검토

프로필에 Figma `03 / Lo-Fi & Wireframes`와 C02~C06 조사 노드가 반영됐고, source-handoff는 자료 조사와 제품 실행 결과를 구분한다. run 기록은 초기 조사 당시 개별 승인을 소급 생성하지 않고 이후 인간 승인과 미승인 UI 계획을 구분한다. 현재 초기 검토에서 QA-001 외 추가 actionable finding은 발견하지 않았다.

## 한계

- 스킬은 Codex가 따르는 실행 절차이며, run.json은 수동 감사 기록이다. 프로그램이 승인 상태를 강제하는 별도 서버·데몬을 검증한 것이 아니다.
- 새 역할 파일의 클라이언트 등록/상시 실행은 검증하지 않았다.
- 실제 Figma·Notion을 QA가 다시 조회하지 않았고 자료 조사 담당의 인수인계와 프로필의 일관성만 확인했다.
- UI 코딩·브라우저 동작·실제 Agent 3 API 연동·운영 DB·외부 모델 품질은 미실행이다.
- 이번 보고의 changes_requested는 구성 수정 요청이며 제품의 승인/반려와 무관하다.
