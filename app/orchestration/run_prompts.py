"""Trusted context for request analysis performed inside the integrated workspace."""
INTEGRATED_REQUEST_CONTEXT = """

이 호출은 변경 요청을 분석하고 고객 협의 문서를 작성하는 서비스 안에서 실행된다.
고객 제품에 구현할 기능과, 이 분석 서비스에게 요구한 결과물 형식을 구분한다.
'공수를 산정하고 협의 메일/변경합의서 초안을 작성해 달라'는 이번 분석 결과물의 형식이다.
이런 지시는 제품에 추가할 function/change_request로 만들지 말고 constraint/constraint로 보존한다.
반대로 '우리 제품에 메일 자동 생성 기능을 추가', '관리자가 합의서를 내려받는 화면 개발'처럼
제품의 새로운 동작을 명시한 요청은 실제 function/change_request로 유지한다.
문서·메일이라는 단어만 보고 기능 요청을 제외하지 않는다. 원문의 표현과 span을 보존한다.
"""
