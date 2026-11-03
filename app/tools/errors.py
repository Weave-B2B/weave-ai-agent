class ToolInputError(ValueError):
    """입력 오류 → 에이전트 오류 코드 INVALID_INPUT (재시도 불가)."""
    code = "INVALID_INPUT"
    retryable = False


class BaselineSuperseded(ToolInputError):
    """다른 기준선으로 대체된(폐기된) 기준선으로 새 판단을 요청함 → 판단하지 않는다."""
    code = "BASELINE_SUPERSEDED"
