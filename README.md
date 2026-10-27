# scope-agent

고객 요청이 기존 계약 범위 안인지 판단하고, 범위 밖이면 공수·금액·일정 영향과 협의용 초안까지 만드는 AI 서버.
**AI공통기준 v0.3 · AI에이전트설계 v0.3 · 평가 데이터셋 v0.5** 에 맞춘 구조다.

```
요청 → ① 요청 구조화 → [PM 항목 확인] → ② 근거 검색·범위 판단 → [PM 검토] → ③ 영향 산정·초안 → [리드 검토] → 완료
```

## 현재 상태

| 구분 | 상태 |
|---|---|
| 공통 스키마 (`schema/schemas.py` 0.3.0) | 제공본 그대로. sha256을 테스트가 검사 |
| 도구 (calc_cost · get_schedule · get_section · verify_quote · list_baseline_docs · search_docs) | 구현. 데이터셋 정답으로 검증 |
| 가드 (② 인용 검증·근거 없음→unclear, ③ LLM 금액 거부·calc_cost 부착) | 구현 |
| 오케스트레이션 엔진 (멈춤·재개·재실행·무효화·호출 전 검사) | 구현 (프레임워크 독립, 메모리 저장) |
| API (`/cases/{id}/run · state · resume · questions/{qid}/answer · retry`) | 구현 |
| ①②③ LLM 에이전트 | **미구현**. 인터페이스·프롬프트·가드만 있음. `AGENT_MODE=fixture`로 정답을 돌려주는 가짜 에이전트 사용 |
| LangGraph 부모 그래프 · Postgres 체크포인터 | 미구현 (엔진의 전이 규칙을 노드로 옮기면 됨) |
| 검색 | 문자 bigram 어휘 검색 기준선. 하이브리드·RRF·리랭크·pgvector로 교체 예정 |

## 구조

```
schema/schemas.py        공통 스키마 원본. 수정 금지 (변경은 공통기준 13장 절차)
app/
  main.py · api/         오케스트레이션 API (routes, models)
  orchestration/         engine · preflight(③ 호출 전 검사) · state_store · ids
  agents/
    base.py · registry.py · fixture.py
    agent1/ agent2/ agent3/   각 agent.py · prompts.py · guards.py(②③)
  tools/                 calc_cost · get_schedule · doc_tools(search/get_section/verify_quote/list_baseline_docs) · trace
  kb/loader.py           지식베이스 로더 + 검색 허용 목록
  llm/client.py          LLM 래퍼 (예정)
data/
  kb/                    sections·documents·baselines·rate_cards·schedules·doc_pages   ← 검색 허용
  documents/             PD 원본 PDF 13개 + 해시                                          ← 검색 허용
eval/
  dataset/               v0.5 스냅샷 (정답·chain·noisy·contract). 색인·프롬프트 예시 금지
  run_eval.py            개발용 평가 (검색 Hit@k, 호출 전 검사)
tests/                   42개
```

## 데이터셋을 쓰는 방식

1. **스키마**: `schema/schemas.py`를 그대로 가져와 입출력 검증에 사용한다. 데이터셋 manifest의 해시와 다르면 테스트가 실패한다.
2. **KB**: `data/kb/*`와 PDF만 검색 대상이다. `eval/`·gold·chain·noisy·contract는 로더가 읽을 수 없고, 테스트가 이를 검사한다.
3. **Fixture**: 개발 초기에 오케스트레이션·API를 LLM 없이 돌리는 가짜 출력. 에이전트 성능 측정에는 쓰지 않는다.
4. **평가**: `eval/dataset`의 split(A=train, B=validation, C=test)을 유지한다. 모든 정답이 이미 노출돼 있어 test를 블라인드 성능으로 주장하지 않는다.

## 코드가 강제하는 규칙

- 입력 스키마가 틀리거나 PM 검토 전이거나 `target_result_id`가 다르면 ③을 호출하지 않는다 (INVALID_INPUT / WAITING_REVIEW / VERSION_CONFLICT).
- 금액은 `calc_cost`만 채운다. LLM 출력에 금액이 있으면 거부하고, `totals`는 도구 호출 기록(result_id·입출력 해시)과 함께 남긴다.
- ② 인용은 `verify_quote`를 통과해야 한다. 통과한 근거가 없으면 `unclear`로 내린다. 기준선 밖·다른 프로젝트 문서는 근거에서 제외한다.
- 산정 대상이 없으면 ③을 호출하지 않고 `NOT_REQUIRED`로 처리한다. `COMPLETED`는 처리 종료이지 확인 질문이 해소됐다는 뜻이 아니다.
- 변경 종류별 재실행·무효화는 `RERUN_TABLE`을 따른다. 무효화된 결과는 삭제하지 않고 `stale_result_ids`와 archive에 보존한다.
- 오래된 화면의 덮어쓰기는 `state_revision`으로 막는다 (HTTP 409).
- 결과 ID·시각은 호출부(엔진)가 발급한다. 에이전트가 만든 값은 덮어쓴다.

## 백엔드 API 명세와의 대응 (참고)

| AI 단계 | 백엔드 명세 | 사람 확인 |
|---|---|---|
| ① 요청 구조화 | CR-11 AI 요청 분해 시작 / CR-13 결과 적용 | PM 항목 확인 |
| ② 범위 판단 | C-01 범위 분석 / C-09 범위 판단 확정 | PM 검토 |
| ③ 영향 산정 | D-01 작업 분해·공수 초안 (금액·일정 재계산은 D-04) | — |
| ③ 초안 | E-01 AI 협의안 초안 | 리드 검토 / D-08 내부 승인 |

## 실행

```bash
pip install -r requirements.txt
python -m pytest -q                 # 42 passed
python -m eval.run_eval             # 검색 Hit@5·MRR, 호출 전 검사 13/13
uvicorn app.main:app --reload       # AGENT_MODE=fixture 로 /docs 에서 흐름 확인
```

`python -m eval.run_eval`의 검색 Hit@5는 기준선당 섹션이 20~30개뿐인 작은 코퍼스라 변별력이 낮다. 회귀 확인용일 뿐 성능 근거가 아니다.

## 팀 확인이 필요한 것

1. **통신 방식**: 백엔드 명세 v0.3은 SQS(C-MQ-01~03), AI에이전트설계 v0.3은 REST(`/cases/...`)다. 지금은 REST 기준이다. SQS를 쓰면 엔진 앞에 어댑터만 추가하면 된다.
2. **PM 항목 확인 입력**: 공통 스키마에 없다. `api/models.py`의 `ConfirmItems`는 임시안이다. 완료 조건, PM이 항목을 고친 뒤 재실행 범위(항목만 정정 vs 의미 변경)도 미정이다.
3. **문서 청킹·임베딩 소유**: AI 서버가 하는지 백엔드가 하는지 미정. AI 서버가 하면 청크 ID·페이지·위치가 백엔드 A-22와 같아야 한다.
4. **A1 운영 질문**: CR-A-001의 사진 포함 여부 같은 비차단 질문을 채택할지 (채택하려면 A1 정답도 함께 갱신).
5. **인젝션 테스트 위치**: 원문 인젝션은 ① 또는 통합 파이프라인에서 테스트한다 (③에는 원문이 안 들어옴).
6. **A3 정답의 지위**: MD·단가·금액·일정은 팀원 임의 참조값이다. MD 정확도·MAPE는 검수 전까지 끈다.
7. **noisy 10건·계약 3건**: typed 기대값이 검수 전이라 평가기에 아직 연결하지 않았다.
