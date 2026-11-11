# Run과 오케스트레이터 연결 — 계약과 AI 서버 연결 구현

작성일: 2026-11-11. **AI 서버의 제안 계약과 로컬 참조 구현**이다. Backend 반영·배포나 통합 화면 전환이 완료됐다는 의미가 아니다.

## 이번 구현 범위

기존 [통합 DTO 설계](dto-integrated-design.md)의 Run ID·결과 참조·revision·operation ID를 유지하면서, 오케스트레이터 실행에 필요한 추가 계약을 별도로 정의했다. 기존 `schema/schemas.py`, 순차 `OrchestrationRuntime` 및 기존 v2 순차 API는 유지한다. 프로토타입의 신규 요청은 아래 로컬 Run 연결 경로로 전환했다.

| 코드 | 역할 |
|---|---|
| `app/orchestration/run_coordination.py` | 실행 소유권, 질문·답변, 신뢰된 조율 상태, 최종 검토 묶음 DTO 및 포트 |
| `app/orchestration/run_agent3_payload.py` | `agent3-run/1` 결과. 기존 잠정 산정의 `needs`·`shared_work_key`와 고객 문서 출처·본문 보존 |
| `app/orchestration/coordinated_execution.py` | 선택된 Agent 한 번 실행 → 변화 비교 → 결과 저장 또는 무변경 종료. 저장 재시도는 모델을 재호출하지 않음 |
| `app/orchestration/fake_run_coordination.py` | 기존 Fake에 분리된 엄격한 참조 구현. 실제 Backend의 구현 완료를 뜻하지 않음 |
| `app/orchestration/http_run_coordination.py` | 추가 HTTP 계약 제안 클라이언트. 기본 비활성, Backend 지원 확인 후에만 명시적으로 활성화 |
| `app/orchestration/run_supervisor.py` | 기존 `choose_action`·Agent 2 도구·Agent 3 잠정 도구를 Run 조회·저장에 연결. 단계마다 실행 객체를 새로 만들어도 결과에서 이어짐 |
| `app/api/run_supervisor_routes.py` | 같은 Run의 자동 실행·단계 실행·질문 답변·최종 검토 API. 기존 프로토타입과 분리 |

## 실행과 무변경 종료

1. 현재 Run revision으로 실행을 획득한다. 서버가 실행 토큰·만료 시각·상위 결과 ID를 고정한다.
2. 오케스트레이터가 선택한 도구를 실행한다. 실행 어댑터 자체가 다음 Agent를 결정하지 않는다.
3. 내용·질문·근거·입력 context가 동일하고 실행 metadata만 다른 경우, `finish_unchanged`로 실행을 닫는다. **기존 결과 ID·결과 revision·하위 결과의 참조는 유지**한다.
4. 실제 내용이 바뀌면 새 결과를 저장하고 하위 결과를 stale 처리한다. 실행 중 상위 결과가 바뀌었으면 이전 실행의 저장을 거부한다.
5. 만료된 실행은 명시적으로 회수하고 새 토큰으로 실행한다. 이전 워커가 최신 revision을 읽어 재제출하더라도 토큰이 달라 거부된다.

현재 비교는 metadata 외의 내용이 하나라도 달라지면 새 결과로 취급하는 보수적인 방식이다. 질문 표현·배열 순서의 의미상 동등성을 LLM으로 판단하지 않는다. 장시간 실행의 lease 갱신과 취소는 후속 구현이다.

## PM 질문과 재개

- 질문은 `question_id`, `item_ids`, 정규화할 `field`, 선택지, 기타 입력 허용 여부, 차단 여부로 관리한다. 동일 항목이어도 다른 field의 질문은 보존한다.
- `item_ids=[]`는 특정 항목에 한정되지 않은 요청 전체 질문이다. Agent 1의 전역 질문도 버리지 않는다.
- 한 요청 버전에서 새 질문은 최대 4개를 보존한다. 기존 질문·답변을 새 상태 쓰기로 삭제하거나 바꿀 수 없다.
- 답변 요청은 질문 ID·답변·행위자·revision·operation ID만 받는다. `quote_links`, 조사 횟수, 호출 예산은 사용자 입력 DTO로 받을 수 없다.
- 답변 기록과 해당 질문 상태 변경은 한 트랜잭션으로 처리한다. `unknown`/‘모름’은 기록하되 차단을 해제하지 않는다. 일반 선택·기타 답변의 업무상 의미 해석은 후속 Agent 분석에서 수행한다.
- 모든 필수 질문에 유효한 답변이 있으면 Run을 RUNNING으로 돌리고 기존 Agent 2·3 결과를 stale로 만든다. **답변 수신은 최종 승인이 아니다.** 현재 참조 구현은 안전하게 전체 A2·A3를 무효화하며, 항목별 영향 범위 최적화는 후속 작업이다.
- 답변은 공통 `Answer` 형식으로도 보존해 다음 Agent 입력에 전달한다. Agent 1 질문이면 A1부터 다시 검토한다. 유효한 답변으로 대기를 마친 경우 연속 실행 구간 카운터만 초기화하고 요청 전체 호출 카운터는 보존한다. ‘모름’은 구간을 초기화하지 않는다.
- 열린 PM 차단 질문이 있으면 Agent 3을 포함한 다음 실행을 시작할 수 없다.

## 최종 검토

Agent 3 실행 완료만으로 Run을 COMPLETED로 만들 수 없다. 고객 초안이 생성된 뒤 최신 A1·A2·A3 결과 ID와 문서 fingerprint를 묶어 검토를 요청한다.

승인 시 같은 묶음과 최신 state revision인지 다시 확인한다. 질문이나 stale 결과, Agent 3의 차단 needs가 남아 있거나 문서가 이전 A2/산정 결과를 참조하면 승인하지 않는다. 승인·보류·반려는 이 전용 경로로만 처리한다. 기존 개별 실행의 PMReview 제출·해제 API로 우회할 수 없다.

문서가 필요한 범위(메일만/메일+합의서)는 서비스 정책과 연결할 후속 항목이다. 현재 검증은 유효한 고객 문서가 한 건 이상 존재하는지를 검사한다. 고객 발송·계약 체결·청구 권한은 부여하지 않는다.

## Backend에 제안하는 추가 API

아래 경로는 **새 제안**이며 기존 Backend에 등록돼 있다고 가정하지 않는다. 기존 `/api/v2/runs` 공개 경로도 이번 작업에서 전환하지 않았다.

| 메서드·상대 경로 (`/internal/v1/runs/{run_id}` 기준) | 의미 |
|---|---|
| `GET /coordination` | Run 상태 + 조율 상태 + 실행 lease + 검토 묶음 조회 |
| `POST /executions/acquire` | CAS로 실행 획득·만료 실행 회수 |
| `POST /executions/commit` | 실행 토큰을 검증해 새 결과 저장 |
| `POST /executions/unchanged` | 결과를 새로 만들지 않고 실행 종료 |
| `POST /coordination` | 신뢰된 AI host의 조율 상태 갱신 |
| `POST /question-answers` | 답변 저장·질문 해제·재개를 원자적으로 처리 |
| `POST /final-review-requests` | 최신 결과·문서 묶음으로 검토 대기 |
| `POST /final-review-decisions` | 해당 묶음의 승인·보류·반려 |
| `POST /llm-attempts` | 모델 API 시도 전 예산을 원자적으로 예약 |
| `POST /resume` | 명시적 보류·실패 재개. 구간 예산만 초기화하고 요청 전체 한도는 보존 |

기존 `X-Internal-Token`·`X-Actor-Member-Id`를 유지한다. 사용자 답변과 서버 조율 상태 쓰기는 Backend에서도 권한을 분리해야 한다. `answered_by`·`reviewed_by`는 인증된 행위자에서 주입해야 하며 브라우저가 임의 지정하게 하지 않는다. Backend 미지원 시 구 경로로 조용히 우회하지 않는다.

Backend는 새 실행 계약을 쓰기 시작한 Run에 구 `start`·결과 커밋·PMReview 해제 쓰기가 섞이지 않도록 실행 모드를 구분하고 거부해야 한다. 클라이언트의 호출 제한만으로 다른 소비자의 구 API 호출을 막을 수는 없다. Fake 참조 구현은 별도 클래스에서 구 쓰기를 거부하며, 실제 Backend의 이 차단까지 완료된 것으로 간주하지 않는다.

## 검증 및 다음 연결 작업

새 회귀 테스트는 `tests/test_run_coordination.py`에서 외부 모델 없이 실행한다. 무변경 종료와 참조 유지, 실행 토큰 회수, 늦은 워커 차단, 질문 답변과 미해결 보존, 오래된 승인 거부, 확장 A3 결과 복원, 저장 재시도 시 모델 중복 호출 방지를 확인한다.

## AI 서버 연결 경로

추가 API는 `/api/v2/runs/{run_id}/supervisor` 아래에 있다. Backend 지원 전에는 기본 설정으로 503을 반환하며 기존 경로로 우회하지 않는다.

| 경로 | 동작 |
|---|---|
| `GET /supervisor` | 상태·질문·최신 고객 초안·검토 묶음 조회. 실행 토큰은 노출하지 않음 |
| `POST /supervisor/execute` | 질문·최종 검토·보류·한도까지 자동 진행 |
| `POST /supervisor/step` | 현재 상태에서 다음 행동 한 번 선택·실행 |
| `POST /supervisor/answers` | 인증된 행위자로 답변 저장 |
| `POST /supervisor/final-review` | 정확한 결과·문서 묶음 승인·보류·반려 |

`execute`와 `step`의 본문은 `{"expected_state_revision": 0}`처럼 조회한 revision을 사용한다. `answers`에는 `operation_id`, `question_id`, `answer`, `disposition`을 추가한다. `final-review`에는 `operation_id`, 조회한 `bundle`, `decision`을 추가한다. `answered_by`·`reviewed_by`는 서버가 `X-Actor-Member-Id`에서 주입하며 클라이언트가 지정하면 거부한다. 이 헤더의 실제 사용자 권한 검증은 Backend가 담당한다.

총괄은 기존 `choose_action`을 사용해 현재 허용된 행동 하나를 고른다. 최초 Agent 1 실행만 초기 입력에서 시작하는 부트스트랩이다. 그 뒤에는 매번 결과를 다시 읽고 다음 작업을 선택한다. Agent 2의 PM 확인 질문은 Agent 3 전에 게시한다. 같은 Run 입력·항목에서 추가 조사는 최대 한 번 허용하며, 결과가 같으면 기존 A2/A3 결과 참조를 유지하고 PM 질문으로 전환한다. A1 결과나 PM 답변이 달라진 경우 별도 조사 범위로 구분한다.

Agent 3은 `source`에 A1/A2 결과 ID·답변 ID·단가표 ID·견적 연결·계산 기준 hash만 저장한다. 후속 도구 실행은 이 참조로 이전 입력을 재구성하고 `restore_result`로 산정과 문서를 복원한다. 전체 세션 checkpoint나 이전 Agent의 DTO 전체를 결과 payload에 중첩하지 않는다. 메일 작성만으로 공수 산정을 다시 호출하지 않는다. 입력·공유 작업·전역 조건이 변경된 부분 재산정은 기존 Agent 3 도구 검증을 그대로 따른다.

서버 루프의 연속 실행 12회, 연속 모델 호출 시도 40회, 요청 전체 80회 한도를 연결했다. `StructuredLlm`의 실제 공급자 호출 직전에 공통 Run 포트로 예약하므로 형식 오류에 대한 재시도도 각각 집계한다. 검색·인용 검증 같은 로컬 도구 횟수와 모델 호출 횟수는 구분한다. 임베딩 호출은 이 LLM 카운터에 포함하지 않는다.

프로토타입의 새 요청은 `/prototype/runs/cases`를 사용한다. 과거 브라우저 세션의 `/prototype/supervisor/cases`와 기존 v2 `/orchestrations`·개별 Agent 실행은 유지한다. 새 경로가 실제 Backend에서 동작하려면 제안한 추가 내부 API가 먼저 지원돼야 한다. `.env.example`의 `BACKEND_COORDINATION_ENABLED`를 켜는 것만으로 Backend 기능이 생기지 않는다.

검증: `tests/test_run_supervisor.py`는 Fake 저장 포트와 합성 모델 응답으로 새 실행 객체에서 결과 복원, 자동 실행, PM 질문 전 정지, 답변 후 재조사·재산정·메일 생성, 무변경 재조사 종료, 최종 승인, API 행위자 주입·토큰 비노출을 검사한다. 실제 Backend·외부 모델 호출 검증은 아니다.

이후 작업 순서:

1. Backend 담당자에게 추가 상태·원자적 쓰기·권한 계약을 전달하고 실제 지원 경로를 확정한다.
2. 실제 Backend로 새 `RunSupervisorRuntime`·저장 계약을 연결 검증한다. 현재 연결은 AI 서버와 Fake 계약 테스트까지다. 장시간 실행 lease 갱신·취소, 다중 워커 실행 회수와 부분 재산정 통합 시나리오를 추가 검증한다.
3. 실제 Backend가 같은 예산 예약·재개 계약을 지원하는지 검증한다. AI 서버 로컬 포트에서는 공급자 호출 직전 예약과 PM 답변 후 구간 재개를 구현했다.
4. 로컬 화면에서 검증한 실행·질문·최종 검토 연결을 실제 Backend Run 경로로 전환한다. 기존 개별 테스트 경로와 구별해 검증한다.
5. 실제 Backend와 저장·재시작·중복 요청·부분 재산정 E2E를 수행한다.

현재는 Run 기반 총괄 실행·API와 **로컬 통합 화면·SQLite 저장 연결까지**다. 실제 Backend 연동과 기존 개별 실행의 승인 정책 이관 완료로 보고하지 않는다.


## 로컬 화면 실행·검증 (2026-11-11)

- 실행: `python -m prototype.run_demo --lab --mode fake --port 8772`. 모델 선택 GPT는 서버의 `LLM_API_KEY`로 `gpt-6-luna`, 로컬 LLM은 `gpt-oss:20b`에 연결한다. 화면의 명시적 모델 선택은 CLI의 개별 Agent 기본값과 구분한다.
- 새 요청은 같은 Run 저장 포트의 엄격한 전이 규칙을 SQLite로 영속화한다. 기본 파일은 `.prototype-runs/runs.sqlite3`이며 Git 제외 대상이다. 업무 백엔드 DB 저장이 아니다.
- 요청·답변·검토는 revision과 operation ID로 보호한다. 다른 로컬 프로세스/저장 객체도 SQLite 트랜잭션으로 중복 실행을 막는다. 진행 이벤트는 같은 로컬 저장소에 보존하며 내부 실행 토큰을 브라우저에 노출하지 않는다.
- UI는 자동으로 다음 작업을 호출하고, 필요한 PM 확인만 선택지/기타 입력으로 받는다. ‘모름’은 질문을 해소하지 않는다. 산정 완료 후 Agent 3의 비차단 제안까지 매번 PM 질문으로 승격하지 않는다.
- Agent 1에는 통합 요청 전용 맥락을 추가했다. 결과물인 협의 메일·합의서 작성 요청은 산정 대상 기능이 아닌 결과 형식 조건으로 보존한다. 실제 제품의 문서 자동 생성 기능 개발 요청은 계속 기능 항목으로 분류한다. 기존 개별 Agent 1의 기본 프롬프트는 유지한다.
- 결과는 기존 요청 원문·범위 검토·영향 산정·승인/협의 화면으로 이동한다. 상세 화면과 목록은 Agent 1의 기능 요약으로 짧은 제목을 공유하고, 원문 전체는 원문 탭에 보존한다.

실제 GPT 합성 시나리오: `CR-UI-5E9D48D8`. 요청 항목화, 범위 검토, PM 답변 2회, 재산정, 협의 메일·변경합의서 초안 생성, 일정 미산정 확인 후 최종 검토 저장을 화면에서 확인했다. 모델 호출 시도는 총 29회였다. 결과 8.5 MD·판매가 5,500,000원은 합성 기준의 테스트 값이며 실제 견적이나 계약 승인으로 사용하지 않는다. 이 테스트 이후 비차단 제안의 PM 질문 승격을 막는 회귀 보완을 추가했다.

기존 로컬 Backend `dev`에는 추가 coordination API가 없어 실제 BE E2E는 수행하지 않았다. 서버 간 토큰 설정만으로 해당 API 지원이 생기는 것은 아니다. 로컬 화면에는 ‘로컬 저장 · 백엔드 미연결’을 표시한다.
