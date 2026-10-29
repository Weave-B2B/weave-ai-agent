"""임베딩 클라이언트. EMBED_PROVIDER로 none·fake·openai·bedrock 선택 (LLM provider와 별개 축)."""
from app.embed.base import EmbedConfigError, Embedder, normalize
from app.embed.factory import build_embedder

__all__ = ["EmbedConfigError", "Embedder", "build_embedder", "normalize"]
