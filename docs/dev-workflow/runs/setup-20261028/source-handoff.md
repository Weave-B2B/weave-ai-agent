# 자료 조사 인수인계

확인일: 2026-10-28 / Asia/Seoul. 담당: `repo_scout`, `design_research`.
외부 소스는 읽기만 했다. 아래 화면의 동작은 설계상 의도이며 브라우저 실행 결과가 아니다.

## 현재 저장소

- HEAD: `6d69950f47e4e5ce0c3e191ca6cfb441dc4794f7`, 브랜치 `feat/#5-agent3`.
- Agent 3: `app/agents/agent3/agent.py`, `workflow.py`.
- 기존 API: `app/api/agent3_routes.py`; 산정 생성·조회·공수 수정·승인/반려.
- 기존 확인 화면: `app/demo_agent3.py`, `app/demo_streamlit.py`.
  이 화면은 API 대신 `Agent3Workflow`를 직접 실행한다.
- 최신 구체 설명은 `docs/agent3/agent3-mvp.md`. README 뒤쪽에 미구현이라는 오래된 표현이 섞여 있다.
- `schema/schemas.py`, 공통 오케스트레이션, A1/A2와 데이터 딕셔너리는 협업 영역이다.
  이번 구성 작업에서 이 영역과 앱 코드를 변경하지 않는다.

## Notion: 실제 fetch한 주요 자료

| 문서 | 확인 근거 | 남은 경계 |
|---|---|---|
| [AI 연동 회의 — A3 담당 준비 자료](https://app.notion.com/p/778a8da52356833583bc01710903b92f) | PM 확인 및 `estimate=true` 대상, 미검토/구버전 차단, 금액은 코드 계산 | 확정·제안·미확정 혼재; 단가·공수는 가상 참조 |
| [화면기획서 분석 - AI 기능](https://app.notion.com/p/13da8da5235683aaad3381ffb90d98b1) | C05 MD·가정·일정, C06 사람이 확정한 버전의 협의안 | 실제 HTTP 계약·디자인 치수는 없음 |
| [MVP 2주차 일정·범위 확인 v0.3](https://app.notion.com/p/95da8da5235683fb869a01c7934db4c9) | PM 항목 확정, 질문 영향 항목만 보류, A3 실무 검토와 상업 승인 별도 | 초기 MVP A1/A2 중심이며 A3는 확장으로 표기; 현재 UI 검증은 사용자 별도 요청 |
| [API 명세 정제본](https://app.notion.com/p/e12a8da523568216981c81a04882369e) | D-01/D-03/D-04/G-17 기능 소유 목록 | 실제 URI·응답 스키마가 아니므로 기존 `/agent3`를 대체하지 않음 |
| [엔티티 뼈대](https://app.notion.com/p/e28a8da5235682c3badc01e5d634ba28) | B/C/D/A 소유 분리와 ERD v0.6 참조 | RAW·진행 중; 예시와 합의 혼재 |

ERD 제안 DB는 스키마만 확인했으며 개별 제안 전체의 반영 상태는 조회하지 않았다.
노션의 Figma 임베드는 unknown block이라 직접 URL을 얻지 못했고, 사용자가 파일 링크를 제공했다.

## Figma: 실제 선택 노드 조사

파일: [2팀 Weave](https://www.figma.com/design/Pq2PLWBhkSupb6ThD8oELN/?node-id=242-2370).
사용자 표기는 `03 Lo fi`, 실제 canvas 이름은 `03 / Lo-Fi & Wireframes`.
`242:2370`은 개별 화면이 아니라 페이지다.

| 화면 | 노드 | 주요 요소 | 조사 수준 |
|---|---|---|---|
| C02 변경요청 Inbox | [280:749](https://www.figma.com/design/Pq2PLWBhkSupb6ThD8oELN/?node-id=280-749) | 검색·필터·상태 탭·목록·등록 | design context + screenshot |
| C03 요청 원문 | [280:1126](https://www.figma.com/design/Pq2PLWBhkSupb6ThD8oELN/?node-id=280-1126) | 원문·요청 항목·PM 메모·질문/답변 | design context + screenshot |
| C04 범위 검토 | [280:1352](https://www.figma.com/design/Pq2PLWBhkSupb6ThD8oELN/?node-id=280-1352) | 기준선·AI 제안·PM 판단·근거·확정 | design context + screenshot |
| C05 영향 산정 | [280:1661](https://www.figma.com/design/Pq2PLWBhkSupb6ThD8oELN/?node-id=280-1661) | MD·단가·금액·수정·가정·일정·이력 | design context + screenshot |
| C06 승인·협의 | [280:2010](https://www.figma.com/design/Pq2PLWBhkSupb6ThD8oELN/?node-id=280-2010) | 협의안 버전·복사·미리보기·내부/고객 상태 | design context + screenshot |
| S04 변경요청 등록 모달 | [280:5436](https://www.figma.com/design/Pq2PLWBhkSupb6ThD8oELN/?node-id=280-5436) | 프로젝트·채널·원문·저장/분석 | design context + screenshot |
| S05 근거 드로어 | [280:5826](https://www.figma.com/design/Pq2PLWBhkSupb6ThD8oELN/?node-id=280-5826) | 원문 위치·인용·불일치·제외 | design context + screenshot |
| C07 실행·검수 | 280:2205 | 후속 단계 | metadata only |
| S06 버전 비교 / S11 이력 | 280:5881 / 280:7094 | 후속 보조 화면 | metadata only |

## 다음 담당자가 유지할 차이

1. Figma 예시 ID·7/8MD·금액·+3일은 레이아웃용 데이터다. 실제 fixture/API 결과로 치환해야 한다.
2. 실제 `schedule.business_days=null`은 0일이 아니라 **미산정 + 사유**다.
3. C05의 공수 편집은 기존 `/agent3/.../edit` 재사용을 검토한다. PM 산정 승인과 Figma의
   리드/상업 승인 표기는 동일한 업무 단계로 단정하지 말고 기획 단계에서 구분한다.
4. C04에 `estimate=true`의 직접 선택 UI는 확인하지 못했다. 초기엔 fixture/adapter 공급 여부를 합의한다.
5. 로딩·빈 상태·실패·409 버전 충돌 등의 전용 디자인 변형은 확인하지 못했다. 구현 수용 기준으로 보완한다.
6. A3 성공이 고객 합의·발송·청구 완료로 이어져서는 안 된다.

## 다음 착수 제안 — 아직 미승인

기획 담당이 C02~C05와 C06의 초안 확인 범위 중 첫 구현 묶음을 사용자와 정한다.
A1/A2·CN 소유 화면은 읽기/명시적 mock으로 제공할 범위를 구분하고,
C05 및 C06 초안은 기존 Agent 3 API 연결 후보로 매핑한다.
이 제안은 사용자에게 승인받을 계획이며 화면 구현 완료나 업무 범위 확정이 아니다.
