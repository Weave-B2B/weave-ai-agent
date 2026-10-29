"""임베딩 공통 인터페이스.

모델이 다르면 벡터 공간이 달라 서로 비교할 수 없다. 그래서 색인과 질의는 반드시 같은 model_id·dim이어야 하고,
모델을 바꾸면 전체 재색인한다 (vector_store가 검사한다).
"""
import math
from typing import Protocol


class EmbedConfigError(ValueError):
    """선택한 provider에 필요한 설정이 없다."""


class Embedder(Protocol):
    model_id: str                    # 색인에 기록되는 이름 (provider:model)
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]:
        """텍스트마다 길이 1로 정규화한 벡터를 같은 순서로 돌려준다."""
        ...


def normalize(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v] if n else v
