# 로컬 실행 데이터와 CN 저장 구조 대응

문서 v0.3 · 2026-10-28 · [목록](./README.md)

[workflow.py](../../../app/agents/agent3/workflow.py), [공통 schemas.py](../../../schema/schemas.py), [calc_cost.py](../../../app/tools/calc_cost.py), [trace.py](../../../app/tools/trace.py)를 읽고 작성했다. **대응 후보를 문서화한 것이며 DB 저장 어댑터·migration을 구현한 것은 아니다.**

## 처리 순서

범위 PMReview가 완료된 입력 수신 → 영향 산정 → PM 산정 확인/수정 → 정확한 snapshot 승인 → 내부 협의안 작성.

에이전트의 A3Status, 전용 workflow.phase, 요청 전체 progress_status는 서로 다른 상태다. 공통 오케스트레이션에 새 산정 승인 게이트를 연결하는 일도 별도다.

| 현재 코드 값 | 저장 대상·대응 후보 | 주의점 |
|---|---|---|
| context.case_id / project_id | change_request / project UUID 매핑 | 논리 문자열과 DB PK는 자동 동일하지 않음 |
| context.request_revision | 요청 원문 버전 참조 | review_version_no나 state_revision과 다름 |
| impact.meta.result_id | ai_analysis.ai_analysis_id 대응 | 문자열 result_id와 UUID 매핑 필요 |
| impact.meta.result_revision | 불변 결과 JSON | 미동결 재실행 순번과 업무 검토 버전 구분 |
| _Session.inp | ai_analysis.input_snapshot 또는 불변 참조 | 항목·A2·PMReview·답변·단가 당시 내용 복구 |
| view.impact | ai_analysis.result + estimate_line 투영 | 원본 불변. 합계·가정·일정·출처 유지 |
| view.snapshot_hash | impact_approval.approved_content_hash 후보 | 현재 코드는 context/upstream IDs/estimates/questions를 SHA-256. 운영 해시 규약 확정 전 |
| view.rate_card_fingerprint | 체크포인트/계산 입력의 단가 snapshot 참조 | 현재 파일 내용 해시. 과거 단가 자체를 영속 보관하지 않음 |
| view.session_id | ai_workflow_state.workflow_id 대응 후보 | 로컬 a3-문자열을 UUID FK로 바로 쓰지 않음 |
| view.state_revision | ai_workflow_state.state_revision | DB 원자적 비교·갱신은 미구현 |
| view.phase | ai_workflow_state.phase | 현 로컬 enum의 운영 채택은 미합의 |
| approval.review_id / target_result_id | impact_approval PK / target_ai_analysis_id 매핑 | A2 뒤 범위 PMReview와 구분 |
| approval.reviewer_ref / reviewed_at | company_member_id / decided_at | 문자열은 인증·인가 증거가 아님 |
| approval.approved / reason | approval_status / approval_reason | 현재 코드 bool과 제안 enum 매핑 필요 |
| approval.request_id / request_hash | 승인 중복 방지 키·결정 payload/해시 | 같은 ID·다른 내용은 충돌. 실제 저장 위치 협의 |
| approval.acknowledge_unknown_schedule | 승인 상세 JSON 또는 감사 기록 후보 | v0.7 후속 보완. 미산정 일정 인지 근거 보존 필요 |
| view.draft / draft_result_id | proposal_version + 생성 ai_analysis 대응 | 현재 코드는 별도 Draft. 두 번째 공통 출력 객체는 아직 아님 |
| view.template_version | 협의안 생성 출처 메타데이터 | 모델 호출 없는 템플릿 생성도 출처 보존 |
| history[] | 불변 산정/승인/협의안 이력 | 현재는 메모리. 실제 version/snapshot 저장 구조 필요 |
| TOOL_LOG[] | ai_tool_call 또는 영속 로그 | 현재 해시·상태만 메모리 보관. DB 이행 아님 |

## 현재 구현과 운영 정책의 경계

- 현재 코드에서 승인하려면 모든 선택 산정의 작업·금액이 준비돼야 하고, 열린/답변만 된 blocking 질문이 없어야 한다. 일정이 null이면 acknowledge_unknown_schedule=true를 요구한다. 이 로컬 정책이 CN·PM/PD 공통으로 확정됐다는 뜻은 아니다.
- PM이 공수·작업을 수정하면 코드 재계산·새 result_id·재승인으로 처리한다. 모델 금액을 그대로 승인하지 않는다.
- 같은 단가 ID의 파일 내용이 바뀌면 로컬 승인이 거절된다. 운영에서는 불변 버전·당시 snapshot을 통해 과거 승인과 새 단가 적용을 구분한다.
- 현재 workflow는 메모리와 프로세스 내 Lock을 사용한다. 영속 복구·다중 worker 경합·인증/인가·CN DB 조회 저장은 구현돼 있지 않다.
- 공통 PMReview 복원, 상위 질문 조회·답변 판정, 최신 A2 결과 확인은 호출부·CN·오케스트레이션 계약으로 맞춘다. JSON 모델에 필드가 없는 부분을 이미 구현했다고 기록하지 않는다.

[초기 MVP 실행 설명](../../agent3-mvp.md) 참조. 이 문서 작업에서는 앱 테스트를 재실행하지 않았으며 이전 테스트 통과 기록을 새로운 DB 연동 검증으로 인용하지 않는다.
