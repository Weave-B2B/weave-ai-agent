# ai_analysis — AI 실행·결과

문서 v0.3 · 2026-10-28 · 케클업 / Weave · 공통 테이블 · 에이전트 3 연계 범위

[이 분류 목록](./README.md) · [전체 목록](../README.md) · [출처·판독 한계](../references/README.md) · [실행 JSON 대응](../agent3/runtime_mapping.md)

## 개요

분석 실행의 상태·출처·결과를 보관하는 기존 테이블이다. 에이전트 3는 영향 산정 결과와 승인 후 협의안 생성 결과의 출처를 이 구조에 연결하는 안을 제안한다. 한 행을 전체 흐름으로 쓸지 단계별 실행으로 쓸지는 공통 합의 대상이다. Agent의 비정형 자유 추론 전문을 저장하는 테이블은 아니다.

현재 에이전트 3 연계에서 확인한 컬럼을 정리했다. 이 폴더의 분류는 전용 소유권을 뜻하지 않으며, 기존 ERD 전체 DDL 명세도 아니다.

**값 예시 안내:** UUID·시각·해시는 문서용 가상 값이다. 같은 의미의 참조 ID는 다른 문서에서도 동일하게 썼다. 각 행은 값의 형태를 설명하며 전체 INSERT 또는 실제 승인 기록이 아니다. 상태 코드가 확인되지 않은 곳은 미정으로 남겼다. [예시 ID 설명](../example_values.md) 참조.

## 기존 ERD에서 활용하는 컬럼

**ERDCloud v0.6에서 내보낸 PostgreSQL SQL과 대조했다.** 타입·NULL·PK/FK는 내보내기 정의 기준이며 실제 DB 적용 검증은 아니다. 기본값은 원본에 없으면 임의 지정하지 않는다. UQ·CHECK 주석은 실행 제약과 구분한다.

| 컬럼 | 타입 | 키 | NULL 허용 | 정의 | 값 예시 | 활용·작성 방식 |
|---|---|---|---|---|---|---|
| ai_analysis_id | UUID | PK | 아니오 | DB 분석 기록 ID | `8bc12b27-4197-512c-8e92-0d35cbdd1cae` | 서버 발급. AI result_id와 매핑 |
| review_version_id | UUID | FK → review_version | 아니오 | 분석 대상 검토 버전 | `e23104cf-7dee-57cc-8f0f-8f79e9ed33ad` | 동일 업무 버전의 여러 결과 구분 |
| analysis_type | VARCHAR(30) | — | 아니오 | 처리 단계/유형 | `A3_IMPACT` | 예시 코드는 제안. 기존 enum과 협의 |
| model_id | VARCHAR(255) | — | 예 | 사용 모델 | `null` | 모델을 호출한 실행의 출처; 모델 미호출 실행 예시 |
| prompt_version | VARCHAR(100) | — | 예 | 사용한 프롬프트 버전 | `agent3-impact-doc-example-v1` | 모델 프롬프트와 문서 템플릿을 구분; 문서용 가상 버전명 |
| input_hash | CHAR(64) | — | 예 | 입력 내용 해시 | `bda84215f9db032f5fe77948cb572679e5550f7b6135c843e3e817cc5f034893` | 내용 비교용. 원문 복원은 불가능 |
| analysis_status | VARCHAR(30) | — | 아니오 | 실행 상태 | 미정 — CN 상태 코드 확인 후 작성 | 업무 전체 phase와 구분 |
| result | JSONB | — | 예 | 검증된 운영 결과 원본 | [결과 JSON의 전체 구조](../agent3/json_fields.md) | 완료된 실제 결과는 보존. 미생성/실패 시 null 가능; 값 칸은 큰 객체를 대신해 문서/파일을 연결한 것 |
| error_code | VARCHAR(100) | — | 예 | 기술 실패 코드 | `null` | 판단 보류·일정 미산정은 기술 오류가 아님 |
| retry_count | INTEGER | — | 아니오 | 실제 재시도 횟수 | 0 | DB 기본값 설정 여부는 DDL 확인 |
| analysis_created_at | TIMESTAMPTZ | — | 아니오 | 분석 기록 생성 시각 | `2026-10-28T10:00:00+09:00` | 서버 기록 |
| completed_at | TIMESTAMPTZ | — | 예 | 실행 종료 시각 | `2026-10-28T10:00:00+09:00` | 실행 상태와 함께 관리 |
| requested_by | UUID | FK 주석 → company_member (제약문 없음) | 예 | 실행 요청자 | `4e74e319-db07-5c8e-8ff4-84a8964e9db1` | 자동 실행이면 null 가능. 반영완 기록과 구현 검증은 구분 |
| retryable | BOOLEAN | — | 예 | 재시도 가능 여부 | `false` | null을 자동 true로 해석하지 않음 |

## 추가 제안 컬럼

**아래 이름·타입·키·NULL 정책은 모두 제안이며 CN 합의·DB 구현 완료를 뜻하지 않는다.**

| 컬럼 | 타입 | 키 | NULL 허용 | 정의 | 값 예시 | 활용·작성 방식 |
|---|---|---|---|---|---|---|
| input_snapshot | JSONB | — | 예(제안) | 실행 당시 입력 또는 복원 정보 | [전체 입력 JSON](../../../examples/agent3/input.json) | 불변 저장소로 복원 가능하면 별도 참조로 대체; 값 칸은 큰 객체를 대신해 문서/파일을 연결한 것 |

## 저장·갱신 규칙과 협의 사항

- `result`는 검증된 불변 원본이다. PM 수정으로 덮어쓰지 않고 새 결과와 당시 입력을 남긴다. 조회용 estimate_line·proposal_version은 해당 원본과 연결한다.
- `input_snapshot`을 null로 둘 때는 다른 불변 저장소에서 당시 A2 결과·PMReview·항목·답변·단가를 복원할 수 있어야 한다. 해시만 남기는 것은 불충분하다.
- meta의 schema_version/result_id/result_revision/agent_version/model/upstream_result_ids를 원본 JSON에 보존한다. 이를 전부 신규 정형 컬럼으로 추가할 필요는 없다.
- 토큰·비밀키·평가 전용 정답 필드·불필요한 debug 정보를 운영 저장본에 포함하지 않도록 경계를 정한다. 모델 미호출의 템플릿 생성 단계는 model_id가 null일 수 있다.

## 실행 데이터와의 관계

현재 코드는 산정 Agent3Output을 메모리에 보관한다. 협의안은 별도 Draft와 draft_result_id이며, 완전한 두 번째 Agent3Output을 DB에 쓰고 있지는 않다. ai_analysis 행으로의 저장·코드 매핑은 후속 설계다. [JSON 구성](../agent3/json_fields.md) 참조.
