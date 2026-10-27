# AI평가데이터셋 v0.5 · 20261023 · AI팀

팀 검수 전 초안. **이번에 제공된 ZIP의 실제 schema/schemas.py(0.3.0)를 수정 없이 사용**한다. 이전 v0.4에서 사용한 임시 미러/1.2.0 payload를 폐기하고 제공 스키마에 정렬했다. 공통 기준을 임의 개정한 것이 아니라 데이터셋/검증기 보완이다.

## 구성

| 대상 | 규모 |
|---|---|
| 원본 요청 | 3개 프로젝트 × 10건 = 30건 |
| 기본 기능별 입력/기대 출력 | A1/A2/A3 각각 30쌍, 총 90쌍 |
| 노이즈 | 업로드 자료의 10건 유지 + 모두 typed 기대 출력·검수 rubric 추가 |
| A3 계약 테스트 | 팀원 자료의 13건 유지, estimates 수치/내용 그대로 |
| 추가 계약 테스트 | v0.4에서 중복되지 않는 원문 모호함·근거 누락·검색 실패 3건 |
| 시나리오 합계 | 기본 30 + 계약/노이즈 26 = 56개. 독립 원본 요청은 30건 |
| 원본 문서 | PDF 13개 무변경 사본·해시 |

`review.html`에서 같은 사례의 A1→A2→PM→A3를 검토한다. 비교 이유는 `COMPARISON.md`, 평가/검수 기준은 `EVALUATION.md`, 실행 결과는 `validation_report.json`을 본다.

## 경로

```text
schema/schemas.py                   업로드 ZIP 제공 스키마 원형 유지
json_schema/                       위 MODELS에서 내보낸 JSON Schema
agent1/{train,validation,test}/input.jsonl · gold.jsonl
agent2/{train,validation,test}/input.jsonl · gold.jsonl
pm_review/{split}.jsonl             가상 PM 검토, 실제 승인 아님
agent3/data/inputs.jsonl · gold.jsonl · contract_cases.jsonl
inputs/agent{1,2,3}.jsonl            동일 데이터의 모아보기 사본
gold/agent{1,2,3}.jsonl              동일 데이터의 모아보기 사본
chain/{split}.jsonl                 전체 입력/기대 출력·PM·미해결 질문
noisy/agent{1,2}_input.jsonl         입력+typed expected_output+rubric
contract_tests/focused.jsonl        추가 3건
kb/                                업로드 자료의 문서/섹션/기준선·가상 도구 입력
runtime/documents/                  PD 원본 PDF 13개
runtime/scenarios/                  기대 출력 없는 통합 시작 입력
reference/                         원본 정답/이전 비교·해시·원 생성기 텍스트
tools/build_dataset.py              보완 재생성 (경로 인수 지원, 삭제 명령 없음)
tools/validate.py                   전체 무결성 검증
```

## 이번 수정

1. 실제 제공 스키마 0.3.0으로 통일. 문서 버전은 v0.5이고 스키마 버전은 0.3.0이다.
2. A004의 11/23↔11/24 질문은 계약서를 조회해야 알 수 있어 A1→A2로 이동. 현재 공통 기준의 non-blocking 경로를 유지하고 초안에 미확정 경고를 추가했다.
3. 원문이 바뀐 noisy 입력은 request_revision 증가·새 analysis_id·고유 scenario/result ID. 원문이 같고 A1 출력만 교란된 A2 noisy는 원문 revision을 유지한다.
4. A3 출력의 누락된 meta를 채우고 A2/PM/A3 result 참조를 v05 새 ID로 재연결. 참조된 결과와 PM 검토는 실제 런타임에서 다시 발급한다.
5. A3의 `estimates`는 원본 30건/계약 13건과 동일. MD/직군/금액/일정·산정 정책을 새로 정하지 않았다.
6. 노이즈 10건에 typed 기대 출력 추가. 기존 자연어 expected는 expected_text_original로 보존했다. 표현과 동등 분해는 사람 검토가 필요하다.
7. 전체 dict·공유 복사본·원문 위치·프로젝트/기준선·인용 위치·합성 금액 산술을 교차 검증한다. 형식 검사와 의미적 판단 품질을 혼동하지 않는다.
8. 업로드 생성기의 `/home/claude/...` 경로와 기존 목적지 재귀 삭제는 실행하지 않았다. 원 스크립트는 `.txt`로 reference에 보존하고 경로 인수형 안전 생성기로 대체했다.

## MD와 정답의 지위

**A3 공수는 팀원 임의 참조값이며 합의된 산정 규칙·실측 정답이 아니다.** 숫자를 평균내거나 기존 제 데이터셋의 5MD/다른 MD로 바꾸지 않았다. calc_cost 검증은 주어진 참조 MD×가상 단가의 산술 확인이다. MD 정확도·MAPE·현실성을 입증하지 않는다. 팀/리드 검수 전 공식 성능 보고에 사용하지 않는다.

새 typed gold·질문 이동·PM·노이즈 기대값 모두 review_needed다. 제공 스키마 파일이라는 사실과 팀이 모든 정책을 승인했다는 사실도 다르다. PD 원본 문서 자체가 가상 실험 자료이며 실제 고객 계약/승인이 아니다.

## 개별 평가와 통합

개별 A2/A3는 **팀 검수 후 고정한** 앞 단계 fixture로 평가한다. 현재는 초안이므로 먼저 원문·조건·인용·PM 대상·산정 가정을 함께 검수한다. 정답은 context.scenario_id로 매칭한다.

통합은 runtime/scenarios 시작 입력에서 실제 A1→실제 A2→실제 A2 result_id를 대상으로 한 PM 검토→실제 A3를 연결한다. chain의 gold를 런타임 대체 입력으로 사용하지 않는다. chain은 평가 비교기만 읽는다. 실제 오케스트레이터·모델·PM UI는 이번 작업에 포함하지 않았다.

계약 테스트 invoke_agent3=false이면 호출 전 차단 기대값이다. WAITING_REVIEW 예시 출력은 방어/화면용일 뿐 실제 모델을 호출하라는 뜻이 아니다. unclear/non-blocking→NOT_REQUIRED 경로는 확인 사항이 해결된 것이 아니다. chain.has_unresolved_followup/open_question_ids를 함께 제시한다. COMPLETED는 현재 파이프라인 처리 종료이지 계약·일정 합의 완료가 아니다.

## 실행

```bash
python -m pip install -r requirements.txt
python tools/validate.py
python agent3/scripts/validate.py --selftest
python -m unittest discover -s tests
```

검증은 이 폴더만으로 재현 가능하다. 재생성은 추출된 업로드 자료·이전 v0.4·PD 원본 워크스페이스가 필요하다:

```bash
python tools/build_dataset.py --source /path/to/extracted_v03 --previous /path/to/weave_dataset_v0_4 --pd-source /path/to/pd_dataset_v1
```

전체 빌드는 이 생성기만 사용한다. 보존한 agent3/scripts/build_dataset.py는 원본 참고용이며 단독 실행하면 보완된 ID·질문·meta가 되돌아간다. 수동 검수 수정은 새 버전으로 보존 후 재생성한다.

입력/정답 모아보기 사본과 split 파일을 따로 수정하지 않는다. 생성 후 검증기를 실행한다. 공식 스키마 자체의 빈 문자열/검증 플래그/PM 권한 한계는 `EVALUATION.md`에 기록했으며 파일을 조용히 수정하지 않았다.

## 분할·검색

A=train, B=validation, C=test를 유지하고 파생은 parent split을 상속한다. noisy/contract는 성능 분할에 합산하지 않는 contract_only다. 모든 원본·정답이 이미 노출돼 있어 test를 독립 블라인드 성능으로 주장할 수 없다. 최종 시험은 새 검수 사례로 별도 확보하는 것이 좋다.

검색 허용은 PDF 또는 kb/sections.jsonl·doc_pages.json만이며 프로젝트·기준선·승인 버전 필터를 강제한다. gold/chain/reference/HTML/noisy/contract/evaluation_only를 색인하지 않는다. rate_cards/schedules는 명시적 도구 입력으로만 사용한다. 경로 분리는 운영 접근제어 구현을 대신하지 않는다.
