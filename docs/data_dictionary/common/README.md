# 공통 테이블 데이터 딕셔너리

문서 v0.3 · 2026-10-28 · [전체 목록](../README.md)

프로젝트·검토 버전·직군/단가·분석/질문·산정/협의안처럼 여러 단계에서 함께 사용하는 구조를 둔다. 에이전트 3 연계 과정에서 확인한 컬럼과 추가 제안을 담고 있으며, 팀 전체 공통 명세의 완성·합의를 뜻하지 않는다.

| 테이블 | 역할 |
|---|---|
| [ai_analysis](./ai_analysis.md) | AI 실행·결과 |
| [review_version](./review_version.md) | 검토 업무 버전 |
| [estimate_line](./estimate_line.md) | 추가 작업·직군별 공수 |
| [job_role](./job_role.md) | 직군 코드 |
| [rate_card_version](./rate_card_version.md) | 단가표 버전·단위 |
| [role_rate](./role_rate.md) | 직군별 원가·판매 단가 |
| [proposal_version](./proposal_version.md) | 협의안 초안 버전 |
| [question_answer](./question_answer.md) | 보완 질문·답변·해결 상태 |
| [ai_workflow_state](./ai_workflow_state.md) | PM 승인 대기·재개 체크포인트 |
| [ai_tool_call](./ai_tool_call.md) | calc_cost 호출 출처·입출력 기록 |
| [estimate_line_item](./estimate_line_item.md) | 공유 작업과 요청 항목의 연결 |

기존 구조와 추가 제안은 각 문서에서 구분한다. ai_workflow_state·ai_tool_call은 기존 공통 체크포인트/영속 로그로 대체할 수 있는 후보이고, estimate_line_item은 공유 작업 지원 시에만 적용할 선택적 제안이다. 공통 폴더에 두었다는 이유로 CN 채택 또는 모든 에이전트의 사용이 확정된 것은 아니다.

공통 테이블의 에이전트 3용 추가 컬럼도 이곳의 해당 테이블 문서 한 곳에 작성한다. 에이전트별 폴더에는 같은 정의를 복사하지 않고 링크로 참조한다. [에이전트 3 연결 방식](../agent3/README.md), [출처](../references/README.md), [예시 값](../example_values.md) 참조.
