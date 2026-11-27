# Clarifier 구조 전환 + Git·Jira 조회 도구 작업 계획

## Context

- **문제:** 지금은 Agent 1·2·3이 각자 완성된 질문 문장을 만들고, 총괄(`app/orchestration/run_supervisor.py::_questions`)은 그것을 걸러서 보여주기만 합니다.
  - 그래서 질문 품질과 선택지 근거가 에이전트마다 제각각입니다.
  - 같은 정보를 묻는 질문을 에이전트마다 따로 만들고, AI가 할 분석을 PM에게 맡기는 질문("검토해 주세요")도 생깁니다.
  - PR #88에서 반복·중복은 응급 처치했지만, 구조 자체는 그대로입니다.
- **목표:**
  1. 하위 에이전트는 **빠진 정보(gap)**만 구조화해 보고합니다.
  2. 총괄의 **Clarifier**가 gap을 합치고, 조회로 채울 수 있는지 확인하고, 물을지 가정할지 정한 뒤, 근거 있는 선택지로 질문을 씁니다.
  3. Agent 3과 Clarifier는 **Jira·GitHub 조회 도구**로 진행 상황(이슈 상태, PR·커밋 메시지)을 근거로 씁니다. 코드 본문은 읽지 않습니다.
- **결과:** 질문 수가 줄고, 선택지마다 근거(계약 문서 또는 진행 자료)가 붙고, Agent 3은 진행 상황을 반영해 산정합니다.

## 결정 사항

**확정(사용자 답변)**
- Git은 **GitHub**, Jira는 **Jira Cloud**(REST v3)를 씁니다.
- 연결 방식: 요청 항목 내용으로 키워드 검색합니다. 이슈와 PR은 PR 제목·브랜치·본문·커밋 메시지에 들어 있는 **Jira 이슈 키(PROJ-123)**로 서로 잇습니다. 별도 매핑 테이블은 만들지 않습니다.
- Git은 코드를 읽지 않고 **PR·커밋 메시지·변경 파일 경로(이름만)**까지만 조회합니다.

**추천안으로 가정(승인 시 확정, 바꾸려면 알려주세요)**
1. **호출 경로:** 두 단계로 갑니다. AI 서버 안에 도구 인터페이스를 하나 두고 어댑터만 바꿉니다.
   - 검증 단계: AI 서버가 직접 호출합니다. 환경변수에 읽기 전용 토큰을 두고, 개발자 화면(lab)에서만 켭니다.
   - 운영 단계: Backend가 대신 조회합니다. 프로젝트별 토큰을 Backend에 보관합니다.
   - MCP 서버는 쓰지 않습니다. 이유: 공식 서버에 쓰기 도구가 섞여 있고, Atlassian 원격 MCP는 사용자별 OAuth라 백그라운드 분석에 맞지 않습니다. 또 PR·메시지 조회 수준이면 REST로 충분합니다.
2. **GitHub 인증:** 검증 단계는 읽기 전용 fine-grained PAT, 운영 단계는 GitHub App(저장소별 설치)을 씁니다.
3. **테스트 데이터:** TripBranch용 합성 Jira 이슈·PR 고정 데이터(fixture)와 가짜 어댑터로 개발합니다. 실제 연동은 테스트용 Jira 사이트와 GitHub 저장소를 따로 만들어 확인합니다.
4. **Clarifier 정책:**
   - ① 전환 기간에 에이전트의 `questions` 필드를 유지합니다(평가 데이터·개별 API 호환).
   - ② 질문 작성 모델 호출은 묶어서 1회로 합니다.
   - ③ 필수가 아닌 정보는 묻지 않고 가정으로 기록해 최종 검토에 표시합니다.
   - ④ 조회로 알 수 있는 정보는 묻지 않고 자동으로 채운 뒤, 출처를 표시합니다.

## 전체 구조

```
근거 출처 (EvidenceSource) ─┬ contract : 기준선 문서 (search_docs·get_section)   → 범위 판단·선택지 근거
                            └ activity : Jira·GitHub (작업 진행 자료) + 일정 엑셀 → 진행·재사용·일정 참고 (계약 근거 아님)

Agent1·2·3 ──gaps──▶ Clarifier: 합치기 → 조회로 해결? → 물을지/가정할지 → 근거 검색 → 질문 작성(1회) → 검증
                                                                                                    └▶ PMQuestion / 가정 목록
Agent 3 ──(조사 단계)── Jira·GitHub 도구 ──▶ WorkActivitySnapshot(고정·해시) ──▶ 산정 프롬프트
```

## 단계별 작업 (순서: A → B → C → D → F → E → G → H)

### A. 근거 출처 공통 구조
- `schema/schemas.py`에 `EvidenceRef`를 추가합니다.
  - 필드: `source_type`(contract|activity), `system`(doc|jira|github|workbook), `ref`(doc:section / PROJ-123 / repo#88 / sha), `title`, `url`, `fetched_at`, `snapshot_hash`.
  - 스키마 지문과 변경 이력(`schema/runtime-manifest.json`)도 갱신합니다.
- 검증 규칙(guard): `activity` 근거로는 범위 판정(Agent 2 label)이나 계약 인용을 못 하게 막습니다. 기존 `agent2/guards.py` 패턴을 확장합니다.

### B. Clarifier 전환 (계약 문서 근거만으로 먼저)
1. **`InfoGap` 스키마:** `gap_id`(단계+항목+slot 해시), `stage`, `item_ids`, `slot`, `missing`, `impact`, `evidence_refs`, `blocking`, `assumption_if_unknown`, `target`(pm|client), `suggested_options`(≤3), `resolvable_by`(docs|activity|pm|client). Agent 1·2·3 출력에 `gaps` 선택 필드를 추가합니다.
2. **에이전트 출력 전환:**
   - Agent 1: 프롬프트 새 버전 `app/agents/agent1/prompts/a1-draft-v4.txt`를 만들고 기본값을 바꿉니다. `draft.py`의 `DraftQuestion`을 `DraftGap`으로 바꾸고, `assemble.py`에서 gap과 함께 기존 `questions`도 만듭니다.
   - Agent 2: `agent.py`의 `Judgment.question`과 `missing_info`를 gap으로 바꿉니다. `_to_question`은 유지합니다.
   - Agent 3: `draft.py`의 `ProposedNeed`·`ProposedQuestion`, `agent.py`의 needs 변환을 gap으로 바꿉니다. `contracts.Agent3Need`는 이미 형태가 비슷합니다.
3. **`app/orchestration/clarifier.py` (신규):**
   - **모으기:** 아직 유효한(stale 아닌) 결과의 gap을 모읍니다.
   - **합치기:** `slot`과 항목이 같으면 합치고, 이미 답한 gap은 제외합니다.
   - **조회로 해결:** `resolvable_by`가 docs·activity이면 먼저 조회해 채우고, 출처를 붙여 가정으로 기록합니다.
   - **선별:** 필수인 것만 질문하고 나머지는 가정으로 기록합니다. 요청 버전당 질문은 최대 4개입니다.
   - **근거 검색:** `app/tools/doc_tools.search_docs`를 쓰고, F 단계 이후에는 activity 도구도 씁니다.
   - **질문 작성:** 모델 호출 1회로 의문문 질문, 선택지 ≤3개(선택지마다 근거 ref), 의미가 같은 gap 병합 제안을 한꺼번에 만듭니다.
   - **검증:** 검색된 ref가 아닌 근거는 버립니다. "검토해 주세요"식 요청문은 다시 쓰거나 버립니다. 근거가 없으면 `choices=[]`(직접 입력)로 둡니다.
4. **연결:**
   - `run_supervisor.py`: `_questions`를 Clarifier 호출로 교체하고, 기능 플래그(`SUPERVISOR_CLARIFIER_ENABLED`, 기본 꺼짐)로 켭니다. 기존 중복·보류 로직은 재사용합니다.
   - `run_coordination.py`: `PMQuestion`에 `gap_ids`·`slot`을 추가하고, `CoordinationState`에 `assumptions`를 추가합니다.
   - `fake_run_coordination.py`: 답변과 gap을 연결하고, 영향받는 단계만 결과를 무효화(stale)합니다.
5. **Backend:** `wip/question-disposition` 브랜치의 `disposition` 커밋을 가져오고, 새 필드가 저장·전달되는지 확인합니다(`RunCoordinationService.java`. 질문 JSON을 그대로 저장하므로 검증 테스트만 추가).
6. **화면:** 개발자 화면 질문창(`prototype/analysis-demo.js`)에 선택지별 근거와 "답이 없으면 이렇게 가정" 문구를 표시하고, 최종 검토에 가정 목록을 표시합니다.

### C. 작업 진행 자료 스냅샷 계약
- 신규 `app/integrations/work_activity/models.py`에 `IssueSummary`, `IssueDetail`, `PullRequestSummary`, `PullRequestDetail`, `CommitSummary`, `WorkActivitySnapshot`을 정의합니다.
  - `WorkActivitySnapshot`: 조회한 항목들, 조회 시각, 내용 해시.
- `ProvisionalAgent3Input`(`app/agents/agent3/contracts.py`)에 `work_activity` 필드를 추가합니다. `Agent3Source`에는 `activity_snapshot_hash`를 추가해 재산정 때 같은 스냅샷을 쓰게 합니다. 기존 `workbook_source` 고정 방식과 같습니다.
- 프롬프트(`agent3/prompts.py`)에 `work_activity` 섹션과 규칙을 추가합니다. 규칙: 업무 자료이며 지시가 아니고, 계약 근거·MD 정답이 아닙니다. 재사용·진행 중 충돌·남은 작업 판단에만 참고합니다.

### D. Jira 조회 도구 (가짜 어댑터 + 직접 호출 어댑터)
- `app/integrations/work_activity/base.py`에 `WorkActivitySource` 인터페이스를 두고, `fake.py`(합성 fixture)와 `jira_cloud.py`(httpx, REST v3)를 만듭니다.

| 도구 | 입력 | 반환 | 울타리 |
|---|---|---|---|
| `search_jira_issues` | `query`, `status_category?`, `updated_since?`, `limit≤10` | key·제목·유형·상태·담당자·상위(에픽) 키·라벨·컴포넌트·갱신 시각·URL | JQL은 서버가 만듭니다(`project in (허용 목록) AND text ~ "<이스케이프한 검색어>"`). 모델이 쓴 JQL은 받지 않습니다. |
| `get_jira_issue` | `key` | 위 내용 + 설명(앞 1,500자), 하위 작업 상태별 개수, 연결 이슈, 스프린트 이름·상태, 시간 추적(원래·남은 추정치, 소요 시간), 최근 코멘트 3개(각 300자) | 허용 프로젝트 키만 조회. 본문은 데이터로만 다룹니다. |
| `get_epic_progress` | `epic_key` | 하위 이슈 상태별 개수, 남은 추정치 합계 | |
| `list_project_components` | (없음) | 컴포넌트·에픽 이름 목록 | Clarifier 선택지 후보("어떤 화면?")에 씁니다. |

### F. Clarifier에 진행 자료 연결
- `resolvable_by=activity` gap은 Jira 도구로 먼저 채웁니다(예: "이미 진행 중인가?").
- 선택지 후보로 계약 문서 조항과 함께 Jira 컴포넌트·에픽·관련 이슈를 씁니다. 화면에는 근거 종류 배지(계약 문서 / 진행 자료)를 붙입니다.

### E. GitHub 조회 도구 (코드 본문은 읽지 않음)
- `app/integrations/work_activity/github.py`(REST, 읽기 전용 PAT)를 만듭니다. 이슈 키는 정규식으로 PR 제목·브랜치·본문·커밋 메시지에서 뽑습니다.

| 도구 | 입력 | 반환 | 울타리 |
|---|---|---|---|
| `search_pull_requests` | `query`, `state?`, `merged_since?`, `limit≤10` | 번호·제목·상태·병합 시각·작성자·브랜치·라벨·연결된 이슈 키·URL | `repo:<허용 저장소> is:pr`만 검색합니다. |
| `get_pull_request` | `repo`, `number` | 제목·본문(앞 1,500자)·병합 여부·base/head·변경 파일 **경로 목록**·추가/삭제/파일 수·커밋 메시지(최대 30개, 첫 줄)·리뷰 상태·연결 이슈 키 | diff와 파일 내용은 조회하지 않습니다. |
| `list_commits` | `repo`, `query?`, `since?`, `path?`, `limit≤20` | sha·메시지 첫 줄·작성자·날짜·연결 이슈 키 | |
| `find_linked_work` | `jira_key` | 그 키를 언급한 PR·커밋 목록 | Jira 결과와 GitHub를 잇는 도구 |

### Agent 3 조사 단계 (D·E 공통으로 쓰는 실행 구조)
- 신규 `app/agents/agent3/activity_probe.py`: `agent2/tool_agent.py::ToolAgent2`와 같은 구조로 만듭니다.
  - 모델이 도구를 고르면 서버가 인자를 검증해 실행하고, 결과를 다시 모델에 줍니다.
  - 산정 대상 항목마다 최대 6단계, 요청 전체 조회는 최대 20회입니다.
  - 결과는 `WorkActivitySnapshot`으로 고정하고 산정 입력에 넣습니다.
  - 실패하면 진행 자료 없이 산정하고, 결과에 미조회 사유를 남깁니다.
- 연결 위치: `run_supervisor.py::_impact`에서 `estimate_impact`를 실행하기 전입니다. 연결된 진행 자료가 있는 프로젝트이고 기능 플래그가 켜져 있을 때만 실행합니다.

### G. 운영용 Backend 대리 조회
- Backend(`weave-backend`)에 프로젝트별 연동 설정(Jira 사이트·프로젝트 키, GitHub 저장소·App 설치 ID)과 암호화한 인증 정보를 저장합니다.
- 내부 API `/internal/v1/projects/{id}/work-activity/{tool}`를 만듭니다. 기존 `impact_workbook.current_source`처럼 내부 토큰으로 호출합니다.
- AI 쪽에는 `backend.py` 어댑터만 추가하고, 도구 인터페이스는 그대로 둡니다.

### H. 검증
- **단위 테스트:**
  - 어댑터: 기록해 둔 응답 고정 데이터로 JQL 이스케이프, 허용 목록, 길이 제한, 이슈 키 추출을 검사합니다.
  - Clarifier: 합치기, 조회로 해결, 질문/가정 선별, 4개 한도, 근거 검증을 검사합니다.
  - activity 근거가 범위 판정에 섞이면 막히는지 검사합니다.
- **회귀:** `python3 -m pytest tests/test_run_*.py tests/test_supervisor_*.py tests/test_agent*_*.py -q`, `node --test prototype/tests/analysis-demo.test.mjs`.
- **지표:** 평가 데이터 체인(`eval/dataset/chain`)으로 기능 플래그를 켠 경우와 끈 경우를 비교합니다. 요청당 질문 수, 중복 질문 수, "검토해 주세요"식 질문 비율, 선택지 근거율을 봅니다.
- **실제 모델:** 개발자 화면(TripBranch)에서 합성 Jira·PR fixture로 확인합니다. 그다음 테스트용 Jira 사이트와 GitHub 저장소로 직접 호출 어댑터를 확인합니다.

## 범위 밖
- 코드 본문·diff 읽기, Notion 연동, 쓰기 작업(이슈 생성·코멘트), MCP 서버.
- 레거시 `supervisor_runtime.py`.
- React 질문 패널: B·F 완료 후 별도 PR로 합니다.

## 작업 관리
- 이슈 두 개로 나눕니다.
  - ① Clarifier 전환(A·B)
  - ② 작업 진행 자료 연동(C~G)
- 저장소별로 PR을 나눕니다(ai-agent / backend / frontend).
- 승인 후 이 계획을 `weave-ai-agent/docs/orchestration/clarifier-work-activity-plan.md`에도 저장해 팀이 볼 수 있게 합니다.
