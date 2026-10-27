"""LLM 호출 래퍼 (구현 예정): 구조화 출력(스키마 검증), 타임아웃(단계당 60초), 재시도,
모델·지연·비용 기록(meta.model / latency_ms / cost_usd). 출력 스키마 불일치는 1회 재요청 후 OUTPUT_INVALID."""
