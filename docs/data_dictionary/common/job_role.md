# job_role — 직군 코드

문서 v0.3 · 2026-10-28 · 케클업 / Weave · 공통 테이블 · 에이전트 3 연계 범위

[이 분류 목록](./README.md) · [전체 목록](../README.md) · [출처·판독 한계](../references/README.md) · [실행 JSON 대응](../agent3/runtime_mapping.md)

## 개요

회사별 직군을 정의하는 기존 참조 테이블이다. 에이전트 3의 PM·UX·FE·BE·QA를 DB 직군 UUID와 연결한다. 에이전트가 임의로 직군 레코드를 생성하지 않는다.

현재 에이전트 3 연계에서 확인한 컬럼을 정리했다. 이 폴더의 분류는 전용 소유권을 뜻하지 않으며, 기존 ERD 전체 DDL 명세도 아니다.

**값 예시 안내:** UUID·시각·해시는 문서용 가상 값이다. 같은 의미의 참조 ID는 다른 문서에서도 동일하게 썼다. 각 행은 값의 형태를 설명하며 전체 INSERT 또는 실제 승인 기록이 아니다. 상태 코드가 확인되지 않은 곳은 미정으로 남겼다. [예시 ID 설명](../example_values.md) 참조.

## 기존 ERD에서 활용하는 컬럼

**ERDCloud v0.6에서 내보낸 PostgreSQL SQL과 대조했다.** 타입·NULL·PK/FK는 내보내기 정의 기준이며 실제 DB 적용 검증은 아니다. 기본값은 원본에 없으면 임의 지정하지 않는다. UQ·CHECK 주석은 실행 제약과 구분한다.

| 컬럼 | 타입 | 키 | NULL 허용 | 정의 | 값 예시 | 활용·작성 방식 |
|---|---|---|---|---|---|---|
| job_role_id | UUID | PK | 아니오 | 직군 ID | `e2e16915-55ba-54ce-aec8-d43390270de2` | estimate_line·role_rate에서 참조 |
| company_id | UUID | FK → company | 아니오 | 직군 소유 회사 | `bf969411-9ca9-5808-9acc-a1aeeffd19d3` | 회사 간 직군·단가 혼용 방지 |
| job_role_code | VARCHAR(50) | — | 아니오 | 직군 코드 | BE | Role enum과 명시적 매핑 |
| job_role_name | VARCHAR(100) | — | 아니오 | 표시 직군명 | 백엔드 개발 | 화면·협의안 표시에 활용 |

## 저장·갱신 규칙과 협의 사항

- 회사별 코드 중복 금지 범위·활성/비활성 정책은 DDL 확인 전 미확정이다.
- 현재 코드의 Role 허용값은 PM/UX/FE/BE/QA다. CN의 직군이 더 세분화되어 있으면 매핑·스키마 확장 여부를 협의한다. 기존 enum을 몰래 바꾸지 않는다.
