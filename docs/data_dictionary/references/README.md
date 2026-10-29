# 출처와 확인 범위

작성: 2026-10-28 · [전체 목록](../README.md)

## ERD 원본

- 원본: [ERDCloud — Weave ERD v0.6](https://www.erdcloud.com/d/J5sro4zYdpM2STSro).
- 사용자에게 권한이 부여된 Chrome 탭에서 읽었다. PostgreSQL 형식, PK·FK·비식별 관계 포함, DROP 미포함·전체 엔티티 내보내기를 사용했다. ERD 자체는 편집하지 않았다.
- 참조 파일: [cn_erd_v06_export.sql](./cn_erd_v06_export.sql). **조회·대조용 내보내기 원본이며 실행용 migration이 아니다.** 새 Agent3 테이블/컬럼은 포함하지 않는다.
- 파일 SHA-256: `4ed232d6666146d2193376843ed202ffc9131ff9733df8bd2a643e4ce50a02cd`.
- 원본에는 CREATE TABLE 43개, PRIMARY KEY 정의 43개, FOREIGN KEY 정의 77개가 있다. 이는 설계의 내보내기 상태이며 운영 DB의 적용 상태를 뜻하지 않는다.
- UNIQUE·CHECK·일부 enum 규칙은 COMMENT ON COLUMN에만 있다. 이 텍스트를 UNIQUE/CHECK 제약 생성문이 존재하는 것으로 해석하지 않는다. 예: question_answer의 OPEN/ANSWERED 정합성, review_version의 버전 순번 UQ.
- SQL에 DEFAULT가 없는 컬럼에 now()/0/false/{}/[] 같은 DB 기본값을 임의로 만들어 쓰지 않는다. 앱 기본값과 DB 기본값은 다르다.

## v0.7과 이전 이미지 판독의 차이

| 이전 명칭/해석 | 원본에서 확인한 내용 | 처리 |
|---|---|---|
| estimate_line.billing_rate_snapshot | selling_rate_snapshot | 딕셔너리에 원본 컬럼명 사용 |
| role_rate.billing_rate | selling_rate | 원본 컬럼명 사용 |
| proposal_version.proposal_amount | proposed_amount | 원본 컬럼명 사용 |
| review_recommendation_direction라는 이전 판독 | review_commercial_decision | 원본 컬럼명 사용 |
| review_version.baseline_id NULL 여부가 불명확 | NOT NULL | 원본의 NULL 정책 반영 |
| UQ/CHECK가 반영됐다는 기록 | 해당 규칙은 내보내기 SQL에서 주석으로 확인 | 설계 의도와 실제 DB 강제 구분 |

이 차이는 기존 CN 설계를 바꾸자는 요청이 아니라 **판독 정정**이다. 사용자 지시에 따라 별도의 ERD 수정·추가 초안 v0.7은 수정하지 않았다. 그 문서를 다음에 개정할 때 명칭 정정도 반영할 수 있다.

## v0.7 및 팀 제공 요약

기준 문서명: `02_CN전달_에이전트3_저장데이터_스키마_초안.md`, v0.7, 2026-10-28. 프로젝트 작업 이력 폴더에 있는 원본을 보존했다. SHA-256: `43f9264d65f223942e585f25589a8f708bd37f1186675dae41df5f2e817df56d`.

개인 로컬 경로에 의존하지 않도록 이 Git용 폴더는 저장소 밖 문서를 필수 링크로 삼지 않는다. 신규 impact_approval·원본 연결·단가 단위·질문 보완·공통 체크포인트/도구 로그·선택적 공유 작업 제안은 v0.7에서 가져왔다. 구체화한 신규 필드·NULL·정책도 모두 채택 전 제안이다.

[CN 엔티티 정리](https://app.notion.com/p/e28a8da5235682c3badc01e5d634ba28), [API 명세](https://app.notion.com/p/e12a8da523568216981c81a04882369e), [ERD 추가 제안 DB](https://app.notion.com/p/977a8da52356836a883801995ff0315a)는 사용자가 제공한 대조 요약의 출처다. 이번 작업에서 Notion 원문을 직접 재조회한 것은 아니다. ‘반영완’ 기록과 운영 DB 검증을 구분한다.

## 코드

공유 [schemas.py](../../../schema/schemas.py) 0.3.0, [workflow.py](../../../app/agents/agent3/workflow.py), [calc_cost.py](../../../app/tools/calc_cost.py), [trace.py](../../../app/tools/trace.py)를 대조했다. 코드 동작과 신규 테이블을 1:1 구현 완료로 동일시하지 않는다. 실제 회사 단가·고객 계약 원문·인증 정보는 이 폴더에 포함하지 않는다.
