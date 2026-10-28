# question_answer — 보완 질문·답변·해결 상태

문서 v0.3 · 2026-10-28 · 케클업 / Weave · 공통 테이블 · 에이전트 3 연계 범위

[이 분류 목록](./README.md) · [전체 목록](../README.md) · [출처·판독 한계](../references/README.md) · [실행 JSON 대응](../agent3/runtime_mapping.md)

## 개요

검토 버전·요청 항목별 질문과 답변을 보관하는 기존 공통 테이블이다. 앞 단계 질문과 에이전트 3 질문을 함께 조회할 수 있지만 발생 단계와 원래 질문 ID는 유지한다. 답변 접수와 문제 해결을 구분하는 값·이력을 추가 제안한다.

현재 에이전트 3 연계에서 확인한 컬럼을 정리했다. 이 폴더의 분류는 전용 소유권을 뜻하지 않으며, 기존 ERD 전체 DDL 명세도 아니다.

**값 예시 안내:** UUID·시각·해시는 문서용 가상 값이다. 같은 의미의 참조 ID는 다른 문서에서도 동일하게 썼다. 각 행은 값의 형태를 설명하며 전체 INSERT 또는 실제 승인 기록이 아니다. 상태 코드가 확인되지 않은 곳은 미정으로 남겼다. [예시 ID 설명](../example_values.md) 참조.

## 기존 ERD에서 활용하는 컬럼

**ERDCloud v0.6에서 내보낸 PostgreSQL SQL과 대조했다.** 타입·NULL·PK/FK는 내보내기 정의 기준이며 실제 DB 적용 검증은 아니다. 기본값은 원본에 없으면 임의 지정하지 않는다. UQ·CHECK 주석은 실행 제약과 구분한다.

| 컬럼 | 타입 | 키 | NULL 허용 | 정의 | 값 예시 | 활용·작성 방식 |
|---|---|---|---|---|---|---|
| question_answer_id | UUID | PK | 아니오 | DB 질문·답변 기록 ID | `a86430c2-e813-56db-9755-ea2b79f85836` | AI question_id와 별도 매핑 |
| review_version_id | UUID | FK → review_version | 아니오 | 관련 검토 버전 | `e23104cf-7dee-57cc-8f0f-8f79e9ed33ad` | 현재 질문 조회 범위 |
| request_item_id | UUID | FK → request_item | 예 | 관련 항목 | `e19e8619-b02e-5636-affb-2e433a2299d7` | 검토 전체 질문이면 null 가능 |
| question | TEXT | — | 아니오 | 질문 본문 | 사진 업로드가 필수인가요? | 발생 시 질문 원본 보존 |
| answer | TEXT | — | 예 | 현재 답변 본문 | 이번에는 사진 제외 | 원본 주석: OPEN이면 답변·시각 null, ANSWERED이면 둘 다 값 있음 |
| question_status | VARCHAR(30) | — | 아니오 | 답변 접수 상태 | `ANSWERED` | AI의 4단계 Question.status와 자동 동일시하지 않음 |
| answered_at | TIMESTAMPTZ | — | 예 | 현재 답변 시각 | `2026-10-28T10:02:00+09:00` | OPEN/ANSWERED 정합성은 CHECK 주석이며 실행 제약은 별도 |
| created_at | TIMESTAMPTZ | — | 아니오 | 질문 생성 시각 | `2026-10-28T10:00:00+09:00` | 답변 시각과 구분 |

## 추가 제안 컬럼

**아래 이름·타입·키·NULL 정책은 모두 제안이며 CN 합의·DB 구현 완료를 뜻하지 않는다.**

| 컬럼 | 타입 | 키 | NULL 허용 | 정의 | 값 예시 | 활용·작성 방식 |
|---|---|---|---|---|---|---|
| source_stage | VARCHAR(30) | — | 예(제안) | 질문 발생 단계 | agent1 | 원래 단계를 유지. 신규 Agent 질문에는 기록 |
| source_ai_analysis_id | UUID | FK → ai_analysis | 예(제안) | 질문 발생 결과 | `008788bf-130e-55ba-90f8-37e92b58baa0` | 재실행해도 생성 출처 보존 |
| ai_question_id | VARCHAR(100) | — | 예(제안) | 원래 AI 질문 ID | CR-A-001-A1Q1 | DB UUID와 매핑. 중복 방지 범위 공통 합의 |
| blocking | BOOLEAN | — | 아니오(제안) | 진행 차단 여부 | `false` | 차단할 단계·승인 정책과 함께 사용 |
| resolution_status | VARCHAR(30) | — | 아니오(제안) | 답변 검토 후 해결 상태 | `RESOLVED` | 정확한 enum과 AI status 매핑은 협의 |
| answer_history | JSONB | — | 예(제안) | 답변·해결 판정 변경 이력 | `[{"answer_id":"answer-example-001","text":"이번에는 사진 제외","answered_by":"pm","answered_at":"2026-10-28T10:02:00+09:00"}]` | 원래 본문·작성자·시각·revision·판정을 추가 기록 |

## 저장·갱신 규칙과 협의 사항

- 저장 장소 후보이지 모든 컬럼의 신설을 요구하는 것은 아니다. 이미 공통 질문 JSON/이력 테이블에서 보장하면 재사용한다. 답변 수정 시 이전 값을 덮어써 없애지 않는다.
- 기존 DB는 단일 request_item_id지만 Question.item_ids는 복수다. 여러 항목에 관련된 질문의 연결 방식은 공통 설계로 정하고 전체 항목 목록을 원본 JSON에 유지한다.
- Question의 reason/field/target/basis_refs/raised_by는 원본 결과 JSON에 보존한다. 정형 컬럼이 없어도 임의 폐기하지 않는다.
- A1의 사진 필수 여부 질문이 열려 있으면 A3는 가정을 표시하고 원 질문을 참조한다. 같은 질문을 새 A3 ID로 복제하지 않는다. Agent3Output.questions에는 stage=agent3만 허용된다.
- ANSWERED가 곧 RESOLVED·산정 가능·승인 가능을 뜻하지 않는다. blocking 질문은 답변 검토·해결 후 필요한 산정을 다시 수행한다. 현재 로컬 A3 API에는 질문 답변 전용 저장·재개 API가 없다.

## 실행 데이터와의 관계

Question/Answer JSON에는 별도 이벤트 ID·작성 주체·출처·verified가 있다. 기존 단일 answer 컬럼만으로 당시 답변을 복원한다고 가정하지 않는다. 입력 snapshot과 불변 답변 이벤트를 연결한다.


**원본 제약 구분:** OPEN/ANSWERED CHECK는 내보내기에서 COMMENT ON COLUMN으로 존재한다. CHECK CONSTRAINT 생성문을 확인한 것이 아니며, 반영완 기록만으로 실제 DB 강제를 단정하지 않는다.
