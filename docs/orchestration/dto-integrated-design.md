# 오케스트레이션·직접 실행 통합 DTO 설계 (v1 확정)

- 작성일: 2026-11-10
- 상태: **코어 Run/실행 DTO v1 확정** (Backend `com.weave.run`·AI 서버 구현, Fake와 실제 Backend live 계약 테스트 통과)
- 소유 구조: **AI Server Orchestration + Backend State Management + Rule-based Workflow + LLM Supervisor**
- 범위: Agent 1 항목화, Agent 2 범위 판정, Agent 3 공수 산출, 오케스트레이션, 자동실행·직접실행 화면
- 관련 문서: [오케스트레이션 설계](./orchestration-design.md), [Backend API 계약](./backend-api-contract.md) (URL·요청/응답·상태 전이·오류의 기준)

## 1. 설계 목표

서비스는 기본적으로 오케스트레이션으로 Agent 1→2→3을 실행하되, 사용자가 Agent를 직접 실행하고 결과를 수정한 뒤 필요한 단계를 다시 실행할 수 있다. 자동실행 화면과 직접실행 화면은 같은 `run_id`의 Run 상태와 결과 이력을 공유한다.

- 오케스트레이션 API: Runtime이 현재 Run 상태를 보고 다음 Agent를 실행한다.
- Agent별 API: 사용자가 지정한 Agent 하나를 실행하거나 결과를 수정한다.
- 두 경로는 같은 Runtime·커밋 규칙·Backend 상태를 사용하며 직접→자동 전환 시 새 Run을 만들지 않는다.
- 필수 필드 검증, revision 검증, 재시도, 상태 전이는 코드가 수행한다(LLM 제외).

## 2. 책임 분리

| 주체 | 책임 |
|---|---|
| Backend | Run 생성·식별, Run 상태와 `state_revision` 영속, Agent별 current/stale 결과 관리, 결과·수정·검토·입력 이력 저장, 권한·상태 전이 검증, 멱등성. 실행 순서는 결정하지 않는다 |
| AI 서버 | Orchestration Runtime(Agent 호출, 상태 전이 요청, 결과 커밋, 재시도, 재개), Agent 1·2·3, Agent 간 데이터 전달. Run 상태·결과를 독자적으로 영속하지 않는다 |
| Supervisor (후속) | 규칙 기반 `next_agent` 자리를 대체해 다음 실행 대상·이유만 판단한다. Runtime이 선행 결과·revision·전이를 검증한 뒤 실행한다 |

실행 흐름: Backend가 Run 생성 → Runtime이 `get_state` → `start_agent` 승인(RUNNING 전이) → Agent 실행 → `commit_result`(`expected_state_revision`, `operation_id`) → `get_state` 반복. 다음 Agent 입력은 Backend에 커밋된 결과를 `get_result`로 조회해 구성한다.

## 3. 현재 구현 상태

### AI 서버

| 구분 | DTO/API | 상태 |
|---|---|---|
| Agent 공통 계약 | `Context`, `Meta`, `Item`, `Question`, `Answer`, `AgentError`, `Evidence` ([schemas.py](../../schema/schemas.py)) | `extra=forbid` |
| Agent 입출력 | `Agent1Input/Output`, `Agent2Input/Output`, `Agent3Input/Output` | 출력은 자기 결과만 담고 이전 결과는 `upstream_result_ids`로 참조 |
| Run 상태 계약 | `RunStateClient`, `RunStateSnapshot`, `HttpRunStateClient`, `FakeRunStateClient` | 응답 `extra=ignore`, 요청 `extra=forbid` |
| Runtime | `OrchestrationRuntime` (`run`/`resume`/`run_agent`/`edit_result`), `workflow_rules` | 규칙 기반 구현 완료 |
| Run API | `/api/v2/runs`: 생성, 조회, `orchestrations`, `pm-review`, `resume`, `agents/{agent}/executions`, `agents/{agent}/edits`, `results/{id}` | 구현 완료, 동기 호출 |
| 구 경로 | `/api/v1` 엔진(`WorkflowState`, `CaseRecord`), 누적 응답 `AnalysisResultDTO` 계열 | 메모리 저장소. v2 Run API는 사용하지 않으며 화면·소비자 이관 후 폐기 |

### Backend

| 구분 | 내용 |
|---|---|
| Run 상태 API | `com.weave.run`, `/internal/v1/runs` 9개 엔드포인트: 상태 조회, 시작, 결과 커밋, 수정, 검토 요청, 검토 해제, 입력 제출·조회, 결과 조회 (Run 생성은 `POST /internal/v1/runs`) |
| 저장 | `analysis_run`, `analysis_run_agent`, `run_result`, `run_operation`, `run_review_resolution`, `run_input` (`db/manual/V20261110__analysis_run_state.sql`) |
| 기존 AI 연동 | C 모듈 `ScopeAnalysisInputBuilder`/`ScopeAnalysisResultReader`와 G의 `ai_analysis`(JSON snapshot)는 별도 경로로 유지 |

## 4. Agent별 DTO

정의 위치: [schema/schemas.py](../../schema/schemas.py). 모든 DTO는 `extra=forbid`다. Agent는 입력 DTO를 받아 출력 DTO 하나를 반환하고, Backend에는 이 출력이 `result_payload`로 저장된다(§5.2).

### 4.1 데이터 흐름

```
원문 메일 ─▶ Agent 1 ─▶ Agent1Output(items, questions) ─▶ Backend 저장(result A1)
                                   │ get_result로 조회
                                   ▼
             Runtime이 범위 대상 항목·제약 항목을 분리해 Agent2Input 구성
                                   ▼
                      Agent 2 ─▶ Agent2Output(assessments) ─▶ Backend 저장(result A2)
                                   │                               │
                                   │                  PM 검토(PMReview, run_input) ◀─ 사람
                                   ▼
             Runtime이 items + A2 결과 전체 + PMReview + rate_card_id로 Agent3Input 구성
                                   ▼
                      Agent 3 ─▶ Agent3Output(estimates, draft) ─▶ Backend 저장(result A3)
```

- Agent 출력은 자기 결과만 담는다. 이전 Agent 결과는 `meta.upstream_result_ids`(ID)로만 참조하며 중첩하지 않는다.
- Agent 입력은 Runtime이 Backend에 커밋된 결과를 조회해 AI 서버 프로세스 안에서 조립한다. Backend로 전송하지 않는다.
- A3 입력의 `a2_result`는 Agent2Output 전체다. A3의 prompt·guard·preflight가 `assessments`, `questions`, `meta.result_id`, `context`를 사용한다.

### 4.2 공통 타입

| 타입 | 필드 | 설명 |
|---|---|---|
| `Context` | `project_id`, `case_id`, `request_revision`(기본 1), `baseline_id`, `baseline_as_of`, `analysis_id`, `scenario_id` | 모든 입출력에 붙는 식별자. 입력의 `context`가 출력에 그대로 이어지며 A3 preflight는 `a2_result.context == context`를 검사한다. `baseline_id`는 A2 필수. `scenario_id`는 운영 입력에서 null |
| `Meta` | `schema_version`, `result_id`, `result_revision`, `created_at`, `upstream_result_ids`, `agent_version`, `model`, `latency_ms`, `cost_usd`, `trace_id`, `debug` | 결과 출처 정보. `result_id`는 호출부가 발급하는 불변 ID. `debug`는 Backend 커밋 시 제거 |
| `Item` | `item_id`, `case_id`, `display_no`, `item_type`, `intent`, `text`, `spans`, `source`, `feature_candidates`, `inferred`, `conditional_followup`, `gold_origin` | 요청 항목 1건. `item_type`: function/constraint/deadline/dependency/question. `intent`: change_request/estimate_only/confirmation/defect/issue_report/status_check/constraint/estimate_request. `gold_origin`은 평가 전용(커밋 시 제거) |
| `Span` | `start`, `end`, `text` | 원문 위치(코드포인트, 0부터, 끝 미포함) |
| `Question` | `question_id`, `stage`, `raised_by`, `item_ids`, `field`, `text`, `reason`, `basis_refs`, `target`(pm/client), `blocking`, `status` | Agent가 만든 질문. 열린 `blocking` 질문이 있으면 READY 상태가 될 수 없다 |
| `Answer` | `answer_id`, `question_id`, `text`, `answered_by`, `answered_at`, `source_ref`, `verified` | 질문 답변. 재실행 입력에 포함 |
| `Evidence` | `doc_id`, `doc_version`, `section_id`, `page`, `line_start/end`, `locator`, `quote`, `role`, `match_type`, `verified` | 문서 근거 인용. `quote`는 섹션 원문 부분 문자열 |
| `AgentError` | `code`, `message`, `stage`, `retryable` | 기술 실패만. 판단 보류·근거 없음·산정 불가는 오류가 아니다 |

모든 Agent 출력(`Agent1/2/3Output`)은 공통으로 `context`, `meta`, `questions`, `error`, `status`를 가진다. `error`가 있으면 `status=ERROR`다.

### 4.3 Agent 1 — 항목화

| 구분 | 필드 |
|---|---|
| 입력 `Agent1Input` | `context`, `channel`(email/manual), `received_at`, `sender_role`, `sender_name`, `subject`, `message_text`(원문, span 기준), `pm_notes`, `answers`, `channel_src`(평가 전용) |
| 출력 `Agent1Output` | `status`(READY/NEEDS_INPUT/ERROR), `items: list[Item]`, `questions`, `duplicates`(MVP 이후) 외 공통 필드 |

- 출처: 입력은 `RunInputs.agent1_input`(Backend `run_input`)이다.
- 검증: 질문의 `stage=agent1`, 질문이 참조하는 `item_ids`는 `items`에 있어야 한다.

### 4.4 Agent 2 — 범위 판정

| 구분 | 필드 |
|---|---|
| 입력 `Agent2Input` | `context`(baseline_id 필수), `message_text`, `a1_result_id`, `items`(범위 판단 대상), `constraints`(참고용), `answers` |
| 출력 `Agent2Output` | `status`(READY_FOR_REVIEW/NEEDS_INPUT/ERROR), `assessments: list[Assessment]`, `questions` 외 공통 필드 |
| `Assessment` | `item_id`, `label`(in_scope/out_of_scope/unclear/approved_change), `evidence: list[Evidence]`, `rationale`, `evidence_level`, `missing_info`, `uncertainties`, `approved_change_ref`, `confirmation_answer`, `gold_ref`(평가 전용) |

- 입력 구성(Runtime): A1 `items` 중 `intent`가 change_request/confirmation/defect/issue_report/status_check/estimate_only인 항목 → `items`. `item_type`이 constraint/deadline/dependency인 항목 → `constraints`. 견적 요청(`question`) 항목은 넘기지 않는다. `message_text`·`answers`는 `agent1_input`에서 가져오고 `a1_result_id`는 A1 현재 결과 ID다.
- 검증: `unclear`는 `missing_info` 또는 질문 필요, `unclear`가 아니면 `evidence` 필요, `approved_change`는 `approved_change_ref` 필요.

### 4.5 PM 검토 (사람 입력, Agent 아님)

| 타입 | 필드 |
|---|---|
| `PMReview` | `review_id`, `review_revision`, `target_result_id`(검토한 A2 결과), `reviewer`, `reviewed_at`, `status`(PENDING/REVIEWED), `items: list[ItemReview]` |
| `ItemReview` | `item_id`, `ai_label`, `pm_label`, `estimate`(A3 산정 여부), `reason`(라벨을 바꾸면 필수), `evidence_refs` |

- Backend `run_input`에 저장되며 `POST /inputs`로 제출한다. A3는 `target_result_id`가 현재 A2 결과와 같고 `status=REVIEWED`일 때만 시작한다.
- 규칙: `estimate=true`는 `pm_label=out_of_scope`만 가능하다.

### 4.6 Agent 3 — 공수 산출

| 구분 | 필드 |
|---|---|
| 입력 `Agent3Input` | `context`, `rate_card_id`, `received_at`, `items`, `a2_result`(Agent2Output 전체), `pm_review`, `constraints`, `answers` |
| 출력 `Agent3Output` | `status`(IMPACT_DRAFT_READY/NEEDS_INPUT/WAITING_REVIEW/NOT_REQUIRED/ERROR), `estimates: list[Estimate]`, `draft`, `review_required`(항상 true), `billing_authorized`(항상 false) 외 공통 필드 |
| `Estimate` | `item_ids`, `status`(draft/not_estimable), `lines: list[Line]`, `totals`, `totals_source`, `schedule`, `rate_card_id` |
| `Line` | `task`, `item_ids`, `role`(PM/UX/FE/BE/QA), `md`(0.5 단위), `assumption` |
| `Totals` | `md`, `md_by_role`, `cost`, `price`, `margin`, `currency`(KRW), `vat_included`, `rate_card_id`. 금액·MD 합계는 `calc_cost`가 계산하며 LLM이 쓰지 않는다 |
| `Schedule` | `business_days`(null = 산정 불가), `basis`, `assumptions`, `reason` |
| `Draft` | `summary`, `scope`, `impact`, `cost`, `open_questions`, `source_result_ids`, `email_subject`, `email_body`. A2·PM 검토·A3 결과를 복사해 채우며 금액·근거를 새로 쓰지 않는다 |

- 입력 구성(Runtime): `items`·`constraints`는 Agent 2와 같은 분리 규칙, `a2_result`는 A2 현재 결과, `pm_review`·`rate_card_id`는 `RunInputs`에서 가져온다.
- 산정 대상은 `pm_review.items`에서 `estimate=true`인 항목이다.
- 검증: `not_estimable`은 `lines`가 비고 `schedule.reason`이 필요하다. `draft`는 `lines` 필요. `NOT_REQUIRED`는 `estimates`가 빈다.

### 4.7 Backend 저장 형태

| 구분 | 내용 |
|---|---|
| 저장 단위 | Agent 출력 1건 = `run_result` 1행 (`result_id`, `result_revision`, `agent_type`, `result_payload`, `upstream_result_ids`) |
| `result_payload` | Agent 출력 DTO의 JSON. Backend는 해석하지 않는다. 사용자 수정도 같은 형식의 새 revision으로 저장 |
| 커밋 전 정리 | 평가 전용 필드(`gold_origin`, `gold_ref`, `channel_src`) 제거, `meta.debug` 비움 |
| 입력 DTO | 저장하지 않는다. `agent1_input`·`pm_review`·`rate_card_id`만 `run_input`에 저장 |


## 5. Run 상태 DTO (Backend 저장 계약)

### 5.1 RunStateSnapshot

```json
{
  "run_id": "run-...",
  "state_revision": 8,
  "status": "WAITING_PM_REVIEW",
  "agents": {
    "agent1": {"status": "COMPLETED", "current_result_id": "a1-...", "result_revision": 1, "upstream_result_ids": [], "stale": false},
    "agent2": {"status": "COMPLETED", "current_result_id": "a2-...", "result_revision": 2, "upstream_result_ids": ["a1-..."], "stale": false},
    "agent3": {"status": "NOT_STARTED", "current_result_id": null, "result_revision": null, "upstream_result_ids": [], "stale": false}
  },
  "pending_review": {"agent_type": "agent2", "target_result_id": "a2-...", "reason": "...", "kind": "PM_REVIEW"}
}
```

- Run 상태 `RunStatus`: `QUEUED`, `RUNNING`, `WAITING_INPUT`, `WAITING_PM_REVIEW`, `COMPLETED`, `FAILED`.
- Agent 상태: `NOT_STARTED`, `RUNNING`, `COMPLETED`, `FAILED`, `SKIPPED`. 사용자 검토 대기는 Agent 상태가 아니라 Run 상태와 `pending_review`(`kind`: `PM_REVIEW`/`INPUT`)로만 표현한다.
- `current_result_id`/`result_revision`은 마지막 성공 커밋 결과를 가리키며 실패는 포인터를 옮기지 않는다.
- `current_stage`는 두지 않는다. 실행 중인 Agent는 `agents`의 `RUNNING`, 검토 대상은 `pending_review`, 다음 실행 대상은 Runtime이 정한다.
- Agent 의존 순서(agent2←agent1, agent3←agent1+agent2)는 코드 규칙(`AGENT_PREREQUISITES`)이며 Run에 저장하지 않는다.

### 5.2 요청·응답

| DTO | 설명 |
|---|---|
| `StartAgentRequest` | Agent 실행 승인. Backend가 선행 결과·stale·중복 실행을 검증하고 RUNNING으로 전이 |
| `CommitResultRequest` | `result_id`(AI 발급), `upstream_result_ids`, `result_payload`, `execution_status`, `requested_run_status`. `result_revision`은 Backend가 부여 |
| `RecordEditRequest` | 사용자 수정은 새 result revision 커밋(D-5). `target_result_id`, `new_result_id`, `edited_by`, `edited_payload`, `reason`. 하위 결과는 stale 처리 |
| `RequestReviewRequest` / `ResolveReviewRequest` | 검토·입력 대기 요청과 해제. 해제 시 `requested_run_status`로 `RUNNING`/`COMPLETED` 지정 |
| `SubmitInputRequest` | PM 검토 문서 제출. `state_revision`만 증가, Agent 상태·결과는 불변 |
| `StateChangeReceipt` | 변경 후 `state_revision`, `status`, 새 `result_id`/`result_revision`, `replayed` |
| `CommittedResult` | `result_id`, `result_revision`, `agent_type`, `result_payload`, `upstream_result_ids`, `created_at`, `is_current` |
| `RunInputs` / `RunInputsView` | `agent1_input`, `rate_card_id`(Run 생성 시 고정), `pm_review`(최신), `pm_review_revision` |

공통 규칙: 모든 쓰기는 `expected_state_revision`(CAS)과 `operation_id`(멱등, 같은 내용 재전송은 `replayed=true`, 다른 내용은 `OperationConflict`)를 가지며 성공 시 `state_revision`이 정확히 1 증가한다. 오류 코드·HTTP 매핑은 [Backend API 계약](./backend-api-contract.md) 참조.

호환 정책: 응답 DTO는 `extra=ignore`(Backend의 필드 추가에 깨지지 않음), 요청 DTO는 `extra=forbid`.

### 5.3 결과 payload 운영 규칙

- payload는 Agent 출력 스키마(`schema_version` 0.3.0)를 따르며 Backend는 해석하지 않는다.
- 평가 전용 필드(`gold_origin`, `gold_ref`, `channel_src`)와 `meta.debug`는 커밋·수정 시 `to_commit_payload`가 제거한다.
- `Context.scenario_id`는 운영 입력에서 null이어야 한다(출력만 지우면 preflight의 `a2_result.context == context` 검사가 깨진다).
- payload 안의 `meta.result_revision`은 권위가 없다. 권위 있는 값은 `CommittedResult.result_revision`과 receipt다.
- 결과 payload는 이전 Agent 결과를 중첩하지 않는다(A1 1.8KB, A2 1.7KB, A3 3.9KB, 픽스처 CR-A-001 실측). 각 결과의 `context`(약 200B)는 입력과 일치해야 하므로 유지한다.
- A3 입력의 `a2_result` 전체는 prompts·guards·preflight가 사용하므로 유지한다.

## 6. API와 화면 경계

| 화면/기능 | API (`/api/v2/runs`) | 공유 상태 반영 |
|---|---|---|
| 자동실행 시작·계속 | `POST /` → `POST /{id}/orchestrations` | Runtime이 더 진행할 수 없을 때까지 실행, 결과를 같은 Run에 커밋 |
| PM 검토 | `POST /{id}/pm-review` → `POST /{id}/resume` | 검토 제출 후 승인/거절, 승인 시 이어서 실행 |
| Agent 직접 실행·재실행 | `POST /{id}/agents/{agent}/executions` | 같은 Run에 커밋, 선행 결과·stale 검사. 다음 Agent로 이어가지 않음 |
| 결과 수정 | `POST /{id}/agents/{agent}/edits` | 새 result revision 커밋, 하위 결과 stale |
| 결과·상태 조회 | `GET /{id}`, `GET /{id}/results/{result_id}` | current/stale/revision 명시 |
| 자동 화면 → 직접 화면 이동 | 조회 | `run_id`, agent_type/result_id 전달, 새 Run 생성 없음 |

## 7. 미확정·후속

| 항목 | 상태 |
|---|---|
| Supervisor (`SupervisorDecision`: `decision_id`, `run_id`, `based_on_state_revision`, `action`, `agent_type`, `reason`, `required_upstream_result_ids`) | 미구현. Runtime 검증 뒤에 연결 |
| 사용자 추가 입력(`answers`) 저장, INPUT 대기 재개 | 미구현(`resume`은 INPUT 대기를 해제하지 않음) |
| 비동기 Job·상태 조회 | 미구현(현재 동기 호출) |
| RUNNING 복구 필드(lease 등) | 미구현(재시작 후 RUNNING이면 `AGENT_RUNNING`으로 중단) |
| SKIPPED 전이 | DTO로 표현만 가능, 전이 없음 |
| Backend 기존 모델 매핑 | `pm_judgment`·`request_item`·`question_answer` ↔ `run_input`, `rate_card_id` ID 체계, `analysis_id`/`session_id` ↔ `ai_analysis_id`/검토 버전, `request_revision`(현재 입력 빌더가 1 고정), 항목 intent/type enum |
| Agent 3 세션 ID 은닉 | 미구현 |
| 구 경로 폐기 | `/api/v1/analyses`, `AnalysisResultDTO` 계열, `AnalysisService`, `PrototypeResultMapper` 제거. 소비자는 `prototype/api.js`이며 `/api/v2/runs`로 이관하거나 프로토타입을 폐기한 뒤 제거한다. `schedule_reference`(기한·제약 항목의 계약 일정 참고)의 v2 위치와 `ScheduleReferenceDTO.milestones: list[dict]` 정리를 먼저 정한다 |
| 검증 한계 | 권한(401/403)·동시성은 실제 Backend로 검증하지 못함(권한 facade stub) |
| Backend ↔ 프론트 외부 API | 프론트는 Backend만 호출하는 구조로 `AiAnalysisController`와 Run API의 역할 정리 필요 |

## 8. 구현 기록

| 단계 | 내용 |
|---|---|
| 1 | Agent 1·2 단일 실행 primitive(`/api/v2/runs/{id}/agent1/items`, `agent2/assessments`, 상태 비저장) |
| 2 | `RunStateClient` 계약과 Fake, `agents` 확장(`AgentState`) |
| 3 | `start_agent`/`get_result`, 규칙 기반 `OrchestrationRuntime`, `workflow_rules` |
| 3-1 | 수정=새 revision(D-5), `PendingReview.kind`, stale 전파 |
| 4 | Backend `com.weave.run`: Run 생성, 상태·시작·커밋·수정·검토 요청·해제·결과 조회 |
| 5 | `HttpRunStateClient`, live 계약 테스트, Fake `strict_commit` |
| 6 | `Runtime.resume`, `resolve_review.requested_run_status` |
| 7 | 입력 문서 `run_input`, `BackendRunInputProvider`, 응답 호환 정책 |
| 8 | Run API 연결(오케스트레이션·PM 검토·resume), 직접 실행·수정 API, payload 운영 규칙 |

검증 (2026-11-10): AI 전체 682 passed, 기존 실패 6건(`test_agent1_api`/`test_agent2_api` unknown case 404, `test_agent3_streamlit` 3건, `test_dataset_integrity::test_schema_unmodified`) 외 신규 실패 없음. Backend 344건 실패 0. 실제 Backend live: 계약 테스트와 Run API 테스트 통과.
