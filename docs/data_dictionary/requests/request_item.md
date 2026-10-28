# request_item — 버전별 요청 항목

문서 v0.3 · 2026-10-28 · 케클업 / Weave · 요청 관리 · 에이전트 3 입력 참조

[이 분류 목록](./README.md) · [전체 목록](../README.md) · [출처·판독 한계](../references/README.md) · [실행 JSON 대응](../agent3/runtime_mapping.md)

## 개요

변경 요청을 항목별로 구조화한 기존 테이블이다. 에이전트 3는 PM이 산정 대상으로 선택한 기능 항목과 일정·제약 문맥을 사용한다. 견적 문의 자체를 추가 기능 한 건으로 계산하지 않는다.

현재 에이전트 3 연계에서 확인한 컬럼을 정리했다. 이 폴더의 분류는 전용 소유권을 뜻하지 않으며, 기존 ERD 전체 DDL 명세도 아니다.

**값 예시 안내:** UUID·시각·해시는 문서용 가상 값이다. 같은 의미의 참조 ID는 다른 문서에서도 동일하게 썼다. 각 행은 값의 형태를 설명하며 전체 INSERT 또는 실제 승인 기록이 아니다. 상태 코드가 확인되지 않은 곳은 미정으로 남겼다. [예시 ID 설명](../example_values.md) 참조.

## 기존 ERD에서 활용하는 컬럼

**ERDCloud v0.6에서 내보낸 PostgreSQL SQL과 대조했다.** 타입·NULL·PK/FK는 내보내기 정의 기준이며 실제 DB 적용 검증은 아니다. 기본값은 원본에 없으면 임의 지정하지 않는다. UQ·CHECK 주석은 실행 제약과 구분한다.

| 컬럼 | 타입 | 키 | NULL 허용 | 정의 | 값 예시 | 활용·작성 방식 |
|---|---|---|---|---|---|---|
| request_item_id | UUID | PK | 아니오 | 현재 검토 버전의 항목 행 | `e19e8619-b02e-5636-affb-2e433a2299d7` | 산정 행 FK의 대상 |
| review_version_id | UUID | FK → review_version | 아니오 | 소속 검토 버전 | `e23104cf-7dee-57cc-8f0f-8f79e9ed33ad` | 다른 버전 항목을 섞지 않음 |
| request_source_id | UUID | FK → request_source | 예 | 원문 출처 | `f0190294-4a98-5e13-9d21-ee7d9c95bf62` | 원문 위치를 적을 때 연결 필요 |
| stable_item_key | UUID | —; UQ는 주석 | 아니오 | 버전 간 동일 의미 항목 키 | `524a2369-c9d6-576c-8b6c-9d7af6c58e15` | AI item_id와 DB 키 대응은 별도 정의 |
| item_type | VARCHAR(30) | — | 아니오 | 항목 유형 | `function` | 공통 enum과 DB 코드 매핑 |
| content | TEXT | — | 아니오 | 항목 내용 | 구매자 리뷰 기능 추가 | Item.text 투영 |
| source_start | INTEGER | — | 예 | 원문 시작 위치 | 7 | Unicode 코드포인트·0부터 |
| source_end | INTEGER | — | 예 | 원문 끝 위치 | 57 | 끝 미포함. 원문[start:end] 확인 |
| item_origin | VARCHAR(30) | — | 아니오 | 항목 생성 출처 | 미정 — CN 상태 코드 확인 후 작성 | AI/PM 수정 구분 |
| item_edit_reason | TEXT | — | 예 | 항목 수정 사유 | 사진 제외로 범위 수정 | 기존 수정 정책 활용 |
| created_at | TIMESTAMPTZ | — | 아니오 | 생성 시각 | `2026-10-28T10:00:00+09:00` | 이력 확인 |
| created_by | UUID | FK 주석 → company_member (제약문 없음) | 예 | 작성자 | `4e74e319-db07-5c8e-8ff4-84a8964e9db1` | AI 분해 항목은 null이라는 주석 |

## 저장·갱신 규칙과 협의 사항

- Item.spans는 복수지만 기존 source_start/source_end는 단일 구간이다. 복수 원문 위치·source·inferred·intent는 원본 결과/입력 snapshot에 보존하고 단일 구간으로 정보가 전부 표현된다고 가정하지 않는다.
- v0.7에서 request_item 컬럼 신설을 확정한 것은 아니다. A1/A2 공통 항목 구조 협의와 연결하며 에이전트 3만 별도 필드를 임의 추가하지 않는다.
- stable_item_key의 UQ와 원문 위치 CHECK는 원본 SQL에서 COMMENT로만 확인됐다. 실제 제약 적용과 구분한다.

## 실행 데이터와의 관계

PMReview.items[].estimate=true인 item_id에 대응하는 행만 산정한다. 현재 공통 PMReview 검증상 pm_label=out_of_scope인 항목만 estimate=true로 둘 수 있다.
