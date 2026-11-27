# 문서 검토 평가셋 (#89)

문서 검토 에이전트(버전 비교 + AI 다중 문서 검토, #91)를 만들기 전에 정답을 고정해 둔 평가셋이다.
정책: 2026-11-27 정책 5·6 — 사용자는 문서만 주고, 무엇을 무엇과 비교할지는 LLM이 판단하며, 빠짐없음은 항목 지도와 확인 체크로 보장한다.

## 구성

| 파일 | 내용 |
|---|---|
| `items/<문서ID>.json` | 저장소 합성 문서(`data/documents`, A 세트)를 새 파서(r2·B1-v3)로 읽은 **항목 지도**. 에이전트 입력과 같은 모양 |
| `cases.jsonl` | 케이스 18개. 문서 버전은 항목 지도에 ops(원문 바꾸기·항목 추가·삭제·번호 바꾸기)를 적용해 만든다 |
| `../../doc_review/build_items.py` | 항목 지도 다시 만들기 (파서가 바뀌면 실행) |
| `../../doc_review/dataset.py` | 케이스 → 문서 버전 항목 지도 |
| `../../doc_review/score.py` | 채점 |

## 문제 종류 (`type`)

| type | 화면 이름 | 예 |
|---|---|---|
| `CHANGE` | 변경 사항 (되돌림 포함, `change: added/removed/reverted`, `value_change`) | 계약서 V1→V2 제4조 금액 변경 |
| `SCOPE_MISMATCH` | 범위 불일치 | SOW 포함인데 명세 OUT |
| `MISSING` | 누락 | 견적에만 있는 기능 |
| `VALUE_MISMATCH` | 수치·일정·조건 불일치 | 견적 공급가액 ≠ 계약 금액 |
| `EFFECT_APPROVAL` | 효력·승인 문제 | 기준일이 변경합의서 효력일 전 |
| `PRIOR_JUDGMENT_IMPACT` | 기존 AI 판단 영향 | CR-07 근거 조항이 새 버전에서 바뀜 |
| `UNCHECKED` | 미확인 | 추출 실패 문서 |

## 케이스

| 범위 | ID | 확인 |
|---|---|---|
| 버전 | V01–V09 | 조건 수정·추가·삭제·되돌림(3버전)·표현만 변경(음성)·금액 변경·**조항 삽입으로 번호 밀림(음성)·번호 다시 매김(음성)·같은 번호에 다른 기능** |
| 다중 | M01–M08 | 누락, 범위 불일치, 수치 불일치, 효력일 전/후(M04a·M04b 쌍), 세 문서 대조, 추출 실패, 기존 판단 영향, 일관된 문서(음성) |

케이스 필드: `as_of`(판정 기준일), `documents`(ref·version_no·ops·extraction_failed), `prior_judgments`(M07),
`expected`(`required=false`는 찾으면 가점인 선택 후보), `forbidden`(음성 사례에서 보고하면 안 되는 후보).

## 채점

```bash
python -m eval.doc_review.score predictions.json
```

- `recall_required`: 필수 후보를 찾은 비율 (type 일치 + 문서 하나 이상 + 항목 코드 하나 이상 일치, 예측에 change를 적었으면 기대 change와 일치)
- `forbidden_hits`: 음성 사례 오탐 수 (표현만 바뀌거나 번호만 바뀐 변경을 `minor=true`로 접으면 제외)
- `extra_for_review`: 정답에 없는 나머지 후보 — 자동 오답이 아니라 **사람 검수 대상**
- `unchecked_recall`: 미확인 표시 정확도

## 한계

- A 세트 합성 문서 5종만 쓴다. TripBranch·KMS 팩은 저장소에 없어 2차에 케이스를 더한다(같은 형식, 로컬 경로).
- 버전은 PDF가 아니라 항목 지도 수준에서 만든 변형이다. 파서까지 포함한 끝단 평가는 실제 개정 PDF가 생기면 추가한다.
- 정답은 작성자 1인 기준(검수 전). M08처럼 문제가 없다고 둔 사례의 추가 후보는 사람이 확인해 정답을 보강한다.
