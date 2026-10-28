# role_rate — 직군별 원가·판매 단가

문서 v0.3 · 2026-10-28 · 케클업 / Weave · 공통 테이블 · 에이전트 3 연계 범위

[이 분류 목록](./README.md) · [전체 목록](../README.md) · [출처·판독 한계](../references/README.md) · [실행 JSON 대응](../agent3/runtime_mapping.md)

## 개요

단가표 버전별 직군 단가를 보관하는 기존 테이블이다. 모델은 단가를 생성하지 않는다. calc_cost는 조회한 값으로 공수별 원가·판매 산정가를 계산한다.

현재 에이전트 3 연계에서 확인한 컬럼을 정리했다. 이 폴더의 분류는 전용 소유권을 뜻하지 않으며, 기존 ERD 전체 DDL 명세도 아니다.

**값 예시 안내:** UUID·시각·해시는 문서용 가상 값이다. 같은 의미의 참조 ID는 다른 문서에서도 동일하게 썼다. 각 행은 값의 형태를 설명하며 전체 INSERT 또는 실제 승인 기록이 아니다. 상태 코드가 확인되지 않은 곳은 미정으로 남겼다. [예시 ID 설명](../example_values.md) 참조.

## 기존 ERD에서 활용하는 컬럼

**ERDCloud v0.6에서 내보낸 PostgreSQL SQL과 대조했다.** 타입·NULL·PK/FK는 내보내기 정의 기준이며 실제 DB 적용 검증은 아니다. 기본값은 원본에 없으면 임의 지정하지 않는다. UQ·CHECK 주석은 실행 제약과 구분한다.

| 컬럼 | 타입 | 키 | NULL 허용 | 정의 | 값 예시 | 활용·작성 방식 |
|---|---|---|---|---|---|---|
| role_rate_id | UUID | PK | 아니오 | 직군 단가 행 ID | `bb2ae32c-16f1-520b-8719-2c4d5c8edf76` | estimate_line에서 사용 단가 추적 |
| rate_card_version_id | UUID | FK → rate_card_version | 아니오 | 단가표 버전 | `cd57ac4d-6b6c-5cfc-b1cf-3082d30992c6` | 버전별 값 고정 |
| job_role_id | UUID | FK → job_role | 아니오 | 직군 | `e2e16915-55ba-54ce-aec8-d43390270de2` | 직군·회사 대응 확인 |
| internal_rate | NUMERIC(18, 2) | — | 아니오 | 원가 단가 | 490000 | 합성 BE 값, 현재 코드에서는 KRW/MD |
| selling_rate | NUMERIC(18, 2) | — | 아니오 | 판매 단가 | 700000 | 합성 BE 값, 실제 견적 아님 |

## 저장·갱신 규칙과 협의 사항

- 이 페이지는 rate_card_version의 rate_unit을 사용하는 기본안을 설명한다. 직군별 단위가 다르면 rate_unit을 이 테이블에 두는 대안을 CN과 결정한다. 양쪽에 모순되는 값을 만들지 않는다.
- 같은 버전·직군의 중복 단가를 막을 필요는 있지만 실제 UQ 유무는 DDL 확인 대상이다. 참조한 버전은 불변으로 유지하거나 당시 snapshot을 보존한다.
- 현재 합성값: PM 520000/750000, UX 420000/600000, FE 450000/650000, BE 490000/700000, QA 350000/500000 (원가/판매, KRW/MD). 시장 단가의 근거가 아니다.

## 실행 데이터와의 관계

현재 파일의 rates[role].cost → internal_rate, rates[role].price → selling_rate로 연결하는 후보 매핑이다. 현재 DB 어댑터 구현은 없다.
