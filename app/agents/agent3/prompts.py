"""③ 영향 산정 + 초안 프롬프트. LLM은 작업·직군·MD·가정까지만. 금액은 calc_cost만."""

PLAN_SYSTEM = """\
산정 대상 항목을 작업(화면·API·데이터·테스트)으로 분해한다. 규모가 너무 크거나 요구사항이 정해지지 않아 MD를 낼 수 없으면
status=not_estimable로 두고 lines는 비우고 schedule.reason에 이유를 적는다.
"""

ESTIMATE_SYSTEM = """\
직군 PM·UX·FE·BE·QA별 MD를 0.5 단위로 산정하고, 가정은 line.assumption에 명시한다.
금액·원가·마진·합계를 쓰지 않는다(쓰면 검증 실패). business_days가 null이면 산정 불가이지 0일이 아니다. MD를 달력일로 바꾸지 않는다.
일정은 get_schedule 마일스톤과 같은 요청의 deadline·dependency 항목만 근거로 쓴다. 근거가 없으면 null + reason.
산정 입력이 부족하면 질문(stage=agent3)을 만든다. 기본은 blocking=false: 가정을 적고 진행.
"""

DRAFT_TEMPLATE = """\
[요약] {summary}
[범위 판단과 근거] {scope}
[작업·공수·일정] {impact}
[금액] {cost}
[확인 필요 사항] {open_questions}
"""
