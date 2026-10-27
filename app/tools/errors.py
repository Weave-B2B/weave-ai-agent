class ToolInputError(ValueError):
    """입력 오류 → 에이전트 오류 코드 INVALID_INPUT (재시도 불가)."""
    code = "INVALID_INPUT"
    retryable = False
