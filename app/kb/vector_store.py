"""청크 벡터 저장소. 지금은 로컬 JSON 파일 + 메모리 내 전수 비교, DB가 생기면 pgvector 구현을 추가한다.

색인은 미리 만든다 (python -m app.kb.build_index). 검색 시점에는 질의만 임베딩한다.
색인과 질의의 임베딩 모델·차원이 다르면 비교가 무의미하므로 오류로 막는다.
"""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

import numpy as np

from app.embed.base import Embedder


class IndexMismatch(RuntimeError):
    """색인을 만든 임베딩 모델·차원이 지금 설정과 다르다 — 재색인 필요."""


class VectorStore(Protocol):
    model_id: str
    dim: int

    def search(self, query: list[float], allowed: set[str], top_k: int) -> list[tuple[str, float]]:
        """allowed(chunk_id 집합) 안에서 코사인 유사도 상위 top_k (chunk_id, score)."""
        ...


def chunk_text(s: dict) -> str:
    """임베딩할 텍스트. 제목이 조항의 주제를 잘 요약하므로 함께 넣는다."""
    return f"{s['heading']}\n{s['text']}"


def text_hash(t: str) -> str:
    return hashlib.sha256(t.encode("utf-8")).hexdigest()[:16]


class LocalVectorStore:
    def __init__(self, path: Path):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        self.model_id, self.dim = data["model_id"], data["dim"]
        self.items = data["items"]
        self.ids = [it["chunk_id"] for it in self.items]
        self.matrix = np.asarray([it["vector"] for it in self.items], dtype=np.float32).reshape(len(self.ids), self.dim)

    def check(self, embedder: Embedder) -> None:
        if (self.model_id, self.dim) != (embedder.model_id, embedder.dim):
            raise IndexMismatch(f"index={self.model_id}/{self.dim} ≠ embedder={embedder.model_id}/{embedder.dim} — 재색인 필요")

    def search(self, query: list[float], allowed: set[str], top_k: int) -> list[tuple[str, float]]:
        mask = np.fromiter((cid in allowed for cid in self.ids), dtype=bool, count=len(self.ids))
        if not mask.any():
            return []
        scores = self.matrix @ np.asarray(query, dtype=np.float32)
        scores[~mask] = -np.inf                          # 기준선 밖 청크는 후보에도 오르지 않는다
        order = np.argsort(-scores)[:min(top_k, int(mask.sum()))]
        return [(self.ids[i], float(scores[i])) for i in order]


def build_index(embedder: Embedder, sections: dict, path: Path, previous: Path | None = None) -> dict:
    """모든 섹션을 임베딩해 저장한다. 같은 모델·같은 텍스트인 청크는 이전 색인의 벡터를 재사용한다."""
    reuse: dict[tuple[str, str], list[float]] = {}
    prev = Path(previous or path)
    if prev.exists():
        old = json.loads(prev.read_text(encoding="utf-8"))
        if (old.get("model_id"), old.get("dim")) == (embedder.model_id, embedder.dim):
            reuse = {(it["chunk_id"], it["text_hash"]): it["vector"] for it in old["items"]}
    secs = list(sections.values())
    texts = [chunk_text(s) for s in secs]
    hashes = [text_hash(t) for t in texts]
    todo = [i for i, (s, h) in enumerate(zip(secs, hashes)) if (s["chunk_id"], h) not in reuse]
    fresh = dict(zip(todo, embedder.embed([texts[i] for i in todo]))) if todo else {}
    items = [{"chunk_id": s["chunk_id"], "doc_id": s["doc_id"], "section_id": s["section_id"],
              "extraction_version": s["extraction_version"], "text_hash": h,
              "vector": [round(x, 6) for x in (fresh[i] if i in fresh else reuse[(s["chunk_id"], h)])]}
             for i, (s, h) in enumerate(zip(secs, hashes))]
    out = {"model_id": embedder.model_id, "dim": embedder.dim,
           "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "items": items}
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return {"chunks": len(items), "embedded": len(todo), "reused": len(items) - len(todo),
            "model_id": embedder.model_id, "dim": embedder.dim, "path": str(path)}
