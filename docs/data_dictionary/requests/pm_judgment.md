# pm_judgment — 앞 단계 범위 PM 판단

문서 v0.3 · 2026-10-28 · 케클업 / Weave · 요청 관리 · 에이전트 3 입력 참조

[이 분류 목록](./README.md) · [전체 목록](../README.md) · [출처·판독 한계](../references/README.md) · [실행 JSON 대응](../agent3/runtime_mapping.md)

## 개요

범위 판단을 PM이 확인한 내용을 담는 기존 테이블이다. 에이전트 3 영향 산정 후의 impact_approval과 다르다. 에이전트 3는 현재 A2 결과를 대상으로 확정된 범위 PMReview를 입력받아 산정 대상을 선택한다.

현재 에이전트 3 연계에서 확인한 컬럼을 정리했다. 이 폴더의 분류는 전용 소유권을 뜻하지 않으며, 기존 ERD 전체 DDL 명세도 아니다.

**값 예시 안내:** UUID·시각·해시는 문서용 가상 값이다. 같은 의미의 참조 ID는 다른 문서에서도 동일하게 썼다. 각 행은 값의 형태를 설명하며 전체 INSERT 또는 실제 승인 기록이 아니다. 상태 코드가 확인되지 않은 곳은 미정으로 남겼다. [예시 ID 설명](../example_values.md) 참조.

## 기존 ERD에서 활용하는 컬럼

**ERDCloud v0.6에서 내보낸 PostgreSQL SQL과 대조했다.** 타입·NULL·PK/FK는 내보내기 정의 기준이며 실제 DB 적용 검증은 아니다. 기본값은 원본에 없으면 임의 지정하지 않는다. UQ·CHECK 주석은 실행 제약과 구분한다.

| 컬럼 | 타입 | 키 | NULL 허용 | 정의 | 값 예시 | 활용·작성 방식 |
|---|---|---|---|---|---|---|
| pm_judgment_id | UUID | PK | 아니오 | 항목별 PM 판단 ID | `e7700acb-9893-5192-8b97-109ac2bc487f` | 공통 PMReview 논리 ID와 매핑 확인 |
| request_item_id | UUID | FK → request_item | 아니오 | 대상 항목 | `e19e8619-b02e-5636-affb-2e433a2299d7` | 항목별 판단 |
| company_member_id | UUID | FK → company_member | 아니오 | 판단한 PM | `4e74e319-db07-5c8e-8ff4-84a8964e9db1` | 인증된 신원 |
| scope_status | VARCHAR(30) | — | 아니오 | PM 범위 판단 | OUT_OF_SCOPE | 주석의 enum은 IN_SCOPE/OUT_OF_SCOPE/UNCLEAR |
| judgment_reason | TEXT | — | 예 | 판단 사유 | 계약 제외 기능 확인 | AI 판단과 달라질 때 공통 모델은 사유 요구 |
| judged_at | TIMESTAMPTZ | — | 아니오 | 판단 시각 | `2026-10-28T10:02:00+09:00` | 상위 결과·버전과 연결 |

## 저장·갱신 규칙과 협의 사항

- 이 6개 컬럼만으로 PMReview의 review_id/review_revision/target_result_id/status/estimate 선택/evidence_refs 전부가 표현되지는 않는다. A2 전달 문서와 공통 저장 계약에서 원본 PMReview 또는 복원 가능한 참조를 보장해야 한다.
- 특히 PMReview의 approved_change와 DB scope_status 주석의 3상태는 일치하지 않는다. approved_change 참조를 버리거나 OUT_OF_SCOPE로 바꾸어 재청구하지 않는다.
- PMReview는 영향 산정 승인과 다른 데이터다. 범위 검토 완료만으로 협의안 작성을 허용하지 않는다.
- 판단 근거 연결은 기존 judgment_evidence/item_evidence와 A2 원본 결과에서 보존한다. 에이전트 3가 범위를 다시 판정하거나 원래 인용을 바꾸지 않는다.
