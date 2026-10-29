"""설정으로 임베딩 provider를 고른다. provider를 조용히 바꾸지 않는다 (설정이 빠지면 오류)."""
from typing import Optional

from app.embed.base import EmbedConfigError, Embedder
from app.embed.providers import BedrockEmbedder, FakeEmbedder, OpenAIEmbedder


def build_embedder(settings) -> Optional[Embedder]:
    p = settings.embed_provider
    if p == "none":
        return None
    if p == "fake":
        return FakeEmbedder(dim=settings.embed_dim)
    if p == "openai":
        key = settings.embed_api_key or settings.llm_api_key           # 같은 OpenAI 키를 쓰는 경우가 많다
        missing = [n for n, v in (("EMBED_MODEL", settings.embed_model), ("EMBED_API_KEY", key)) if not v]
        if missing:
            raise EmbedConfigError(f"Missing required setting(s): {', '.join(missing)}")
        return OpenAIEmbedder(base_url=settings.embed_base_url, api_key=key, model=settings.embed_model,
                              dim=settings.embed_dim)
    if p == "bedrock":
        if not settings.embed_model:
            raise EmbedConfigError("Missing required setting(s): EMBED_MODEL")
        return BedrockEmbedder(model=settings.embed_model, dim=settings.embed_dim, region=settings.aws_region)
    raise EmbedConfigError(f"Unknown EMBED_PROVIDER: {p}")
