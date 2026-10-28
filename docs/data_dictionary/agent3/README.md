# 에이전트 3 데이터 연결과 전용 승인 제안

문서 v0.3 · 2026-10-28 · 케클업 / Weave · 담당: 임기민 · [전체 목록](../README.md)

에이전트 3의 **영향 산정 → PM 확인·수정·승인 → 내부 협의안 작성** 연결을 설명한다. 이 폴더에는 해당 단계의 산정 승인 제안과 실행·JSON 문서를 둔다. 공통 테이블 정의는 해당 분류를 링크로 참조한다.

## 이 폴더의 문서

| 문서 | 내용 |
|---|---|
| [impact_approval](./impact_approval.md) | 협의안 작성 전 산정 PM 승인 테이블 제안. 채택 전이며 기존 공통 승인 구조로 대체하는 안도 협의 가능 |
| [실행 데이터 대응표](./runtime_mapping.md) | 로컬 메모리/API 데이터와 CN 저장 구조의 대응 후보 |
| [JSON 하위 필드](./json_fields.md) | 입력·결과·질문·협의안 JSON의 의미와 보존 범위 |

## 다른 분류에서 참조하는 데이터

| 처리 구간 | 참조 문서 |
|---|---|
| 변경 요청·항목·앞 단계 PM 범위 판단 | [요청·범위 검토 목록](../requests/README.md) |
| 업무 검토 버전·분석 원본 | [review_version](../common/review_version.md), [ai_analysis](../common/ai_analysis.md) |
| 작업·공수·직군·단가 | [estimate_line](../common/estimate_line.md), [job_role](../common/job_role.md), [rate_card_version](../common/rate_card_version.md), [role_rate](../common/role_rate.md) |
| 질문·답변·대기·재개 | [question_answer](../common/question_answer.md), [ai_workflow_state](../common/ai_workflow_state.md) |
| 코드 계산·협의안 결과 | [ai_tool_call](../common/ai_tool_call.md), [proposal_version](../common/proposal_version.md) |
| 여러 항목의 공유 작업을 지원할 때 | [estimate_line_item](../common/estimate_line_item.md) — 선택적 확장 |

공통 테이블에 에이전트 3 연계를 위한 컬럼이 추가돼도 정의는 공통 테이블 문서에서 관리한다. 현재 코드의 메모리 구현, CN 제안, 실제 DB 적용은 구분한다. 산정 승인은 A2 뒤 범위 PMReview, 협의안 작성 뒤 internal_approval, 고객 승인과 각각 다르다.

[예시 값·ID](../example_values.md) · [출처·원본 SQL·판독 정정](../references/README.md)

문서 위치만 재구성했다. 기존 ERD 수정·추가 초안 v0.7, 컬럼 제안·타입·키·NULL·가상 값, 앱 코드와 DB는 변경하지 않았다.
