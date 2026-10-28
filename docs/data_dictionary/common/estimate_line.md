# estimate_line — 추가 작업·직군별 공수

문서 v0.3 · 2026-10-28 · 케클업 / Weave · 공통 테이블 · 에이전트 3 연계 범위

[이 분류 목록](./README.md) · [전체 목록](../README.md) · [출처·판독 한계](../references/README.md) · [실행 JSON 대응](../agent3/runtime_mapping.md)

## 개요

요청 항목에 대한 작업별 직군·공수와 적용 단가를 담는 기존 테이블이다. AI가 작업·MD 초안을 제안하고 금액은 calc_cost 코드로 계산한다. PM 수정 후에는 당시 결과·수정 이유·승인 대상 snapshot을 추적한다.

현재 에이전트 3 연계에서 확인한 컬럼을 정리했다. 이 폴더의 분류는 전용 소유권을 뜻하지 않으며, 기존 ERD 전체 DDL 명세도 아니다.

**값 예시 안내:** UUID·시각·해시는 문서용 가상 값이다. 같은 의미의 참조 ID는 다른 문서에서도 동일하게 썼다. 각 행은 값의 형태를 설명하며 전체 INSERT 또는 실제 승인 기록이 아니다. 상태 코드가 확인되지 않은 곳은 미정으로 남겼다. [예시 ID 설명](../example_values.md) 참조.

## 기존 ERD에서 활용하는 컬럼

**ERDCloud v0.6에서 내보낸 PostgreSQL SQL과 대조했다.** 타입·NULL·PK/FK는 내보내기 정의 기준이며 실제 DB 적용 검증은 아니다. 기본값은 원본에 없으면 임의 지정하지 않는다. UQ·CHECK 주석은 실행 제약과 구분한다.

| 컬럼 | 타입 | 키 | NULL 허용 | 정의 | 값 예시 | 활용·작성 방식 |
|---|---|---|---|---|---|---|
| estimate_line_id | UUID | PK | 아니오 | 작업·공수 행 ID | `655a2c8f-14e4-50c3-b5c1-b6b78dcbd9e7` | 작업 하나와 직군 하나를 식별 |
| request_item_id | UUID | FK → request_item | 아니오 | 산정 대상 요청 항목 | `e19e8619-b02e-5636-affb-2e433a2299d7` | 논리 item_id와 DB UUID 매핑 |
| role_rate_id | UUID | FK → role_rate | 예 | 적용 단가 행 | `bb2ae32c-16f1-520b-8719-2c4d5c8edf76` | 불변 단가 버전으로 역추적 |
| job_role_id | UUID | FK → job_role | 아니오 | 작업 직군 | `e2e16915-55ba-54ce-aec8-d43390270de2` | 공통 역할 BE와 매핑 |
| task | TEXT | — | 아니오 | 추가 작업 설명 | 리뷰 사진 업로드 API | 고객 요청 원문 자체와 구분 |
| estimated_md | NUMERIC(10, 2) | — | 아니오 | 제안 공수 MD | 3.5 | 0 이상·0.5 단위. 모름을 0으로 저장하지 않음 |
| internal_rate_snapshot | NUMERIC(18, 2) | — | 아니오 | 산정 당시 원가 단가 | 490000 | 합성 BE 단가 예시. 단위는 별도로 구분 |
| selling_rate_snapshot | NUMERIC(18, 2) | — | 아니오 | 산정 당시 판매 단가 | 700000 | 합성 예시, 실제 회사 견적 아님 |
| assumptions | TEXT | — | 예 | 공수 산정 가정 | 기존 이미지 저장소 재사용 | 가정이 있으면 보존 |
| estimate_status | VARCHAR(30) | — | 아니오 | 공수 검토 상태 | 미정 — CN 상태 코드 확인 후 작성 | 공유 Estimate.status와 매핑 확인 |
| estimate_edit_reason | TEXT | — | 예 | PM 수정 사유 | 이미지 처리 범위를 반영 | PM 변경 시 이유 기록 제안 |
| created_at | TIMESTAMPTZ | — | 아니오 | 행 생성 시각 | `2026-10-28T10:00:00+09:00` | 추적용 |
| created_by | UUID | FK 주석 → company_member (제약문 없음) | 예 | 작성자 | `4e74e319-db07-5c8e-8ff4-84a8964e9db1` | AI 자동 생성 여부와 구분 |
| confirmed_at | TIMESTAMPTZ | — | 예 | 공수 확인 시각 | `2026-10-28T10:02:00+09:00` | 공수 확인과 전체 산정 승인은 구분 |
| confirmed_by | UUID | FK 주석 → company_member (제약문 없음) | 예 | 공수 확인자 | `4e74e319-db07-5c8e-8ff4-84a8964e9db1` | AI를 PM 확인자로 저장하지 않음 |

## 추가 제안 컬럼

**아래 이름·타입·키·NULL 정책은 모두 제안이며 CN 합의·DB 구현 완료를 뜻하지 않는다.**

| 컬럼 | 타입 | 키 | NULL 허용 | 정의 | 값 예시 | 활용·작성 방식 |
|---|---|---|---|---|---|---|
| source_ai_analysis_id | UUID | FK → ai_analysis | 예(제안) | 이 행의 원본 산정 결과 | `8bc12b27-4197-512c-8e92-0d35cbdd1cae` | 신규 A3 행에는 원본 연결. 과거/수동 데이터 정책 확인 |
| rate_unit_snapshot | VARCHAR(10) | — | 예(제안) | 당시 단가 단위 | `MD` | MD / HOUR. 단가 버전으로 복원 가능하면 중복 생략 |
| hours_per_md_snapshot | NUMERIC | — | 예(제안) | 시간 환산 당시 MD당 시간 | null | MD 단가면 환산하지 않음. HOUR 환산 시 당시 값 보존 |

## 저장·갱신 규칙과 협의 사항

- 양수 MD의 단가가 없으면 총액은 null·보완 질문으로 처리한다. 기존 단가 snapshot은 NOT NULL이므로 미완성 행을 0원으로 채우지 않는다. 결과 JSON에 보관하고 행 저장 보류 또는 nullable+상태 제약 변경을 협의한다.
- `not_estimable`이면 가짜 작업 행을 만들지 않는다. 실제 0MD가 검증된 작업은 미산정과 다르다. 현재 calc_cost는 0MD 직군의 단가 누락을 허용한다.
- 공유 Line.item_ids는 복수 항목을 허용하지만 기존 행의 request_item_id는 단일 값이다. [공유 작업 연결 제안](./estimate_line_item.md) 채택 전에는 공수를 복제 합산하지 않는다.
- 원본 산정 결과 연결만으로 PM 수정본의 역사까지 보장되지는 않는다. 수정 결과·당시 입력/승인 snapshot을 보존한다. 승인 뒤 동결된 업무 데이터 수정은 새 버전 정책을 적용한다.

## 실행 데이터와의 관계

Line.task → task, Line.role → job_role 매핑, Line.md → estimated_md, Line.assumption → assumptions. 합계·마진·VAT는 [결과 JSON](../agent3/json_fields.md)에 보존한다. 기존 D-03/D-04의 수정·재계산 계약과 실제 MVP UI 수정 범위는 별도로 맞춘다.
