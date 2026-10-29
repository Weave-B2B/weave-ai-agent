"""임베딩 provider 구현: fake(테스트용) · openai · bedrock(Titan Text Embeddings v2)."""
import hashlib
import json
import re
from typing import Any, Optional

import httpx

from app.embed.base import normalize

_WS = re.compile(r"\s+")


class FakeEmbedder:
    """API 없이 결정적인 벡터. 글자 bigram을 해시 버킷에 더한다 — 의미 검색이 아니라 배선·테스트용."""

    def __init__(self, dim: int = 256):
        self.dim = dim
        self.model_id = f"fake:bigram-hash-{dim}"

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            t = _WS.sub("", t)
            v = [0.0] * self.dim
            for i in range(len(t) - 1):
                h = int.from_bytes(hashlib.md5(t[i:i + 2].encode()).digest()[:4], "little")
                v[h % self.dim] += 1.0 if h & 1 << 31 else -1.0
            out.append(normalize(v))
        return out


class OpenAIEmbedder:
    """OpenAI 호환 /embeddings. text-embedding-3 계열은 dimensions로 차원을 줄일 수 있다 (Titan v2와 맞추기 쉬움)."""

    def __init__(self, *, base_url: str, api_key: str, model: str, dim: int, batch: int = 64,
                 timeout_sec: float = 30, client: Optional[httpx.Client] = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.dim = dim
        self.batch = batch
        self.model_id = f"openai:{model}"
        self.client = client or httpx.Client(timeout=timeout_sec)

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch):
            chunk = texts[i:i + self.batch]
            r = self.client.post(f"{self.base_url}/embeddings",
                                 headers={"Authorization": f"Bearer {self.api_key}"},
                                 json={"model": self.model, "input": chunk, "dimensions": self.dim})
            r.raise_for_status()
            data = sorted(r.json()["data"], key=lambda d: d["index"])
            if len(data) != len(chunk):
                raise ValueError("embedding count mismatch")
            out += [normalize(d["embedding"]) for d in data]
        return out


class BedrockEmbedder:
    """Bedrock Titan Text Embeddings v2 (dimensions 256·512·1024). 요청 하나에 텍스트 하나."""

    def __init__(self, *, model: str, dim: int, region: str, client: Any = None):
        if not model.startswith("amazon.titan-embed-text-v2"):
            raise ValueError("BedrockEmbedder는 amazon.titan-embed-text-v2 계열만 지원")   # 다른 모델은 요청 형식이 다르다
        self.model = model
        self.dim = dim
        self.model_id = f"bedrock:{model}"
        if client is None:
            import boto3
            client = boto3.client("bedrock-runtime", region_name=region)
        self.client = client

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            r = self.client.invoke_model(modelId=self.model, contentType="application/json", accept="application/json",
                                         body=json.dumps({"inputText": t, "dimensions": self.dim, "normalize": True}))
            out.append(normalize(json.loads(r["body"].read())["embedding"]))
        return out
