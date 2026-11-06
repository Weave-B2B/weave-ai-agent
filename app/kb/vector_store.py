"""청크 벡터 저장소. 로컬 JSON 파일(LocalVectorStore) 또는 vector-db의 pgvector(PgVectorStore).

색인은 미리 만든다 (python -m app.kb.build_index). 검색 시점에는 질의만 임베딩한다.
색인과 질의의 임베딩 모델·차원이 다르면 비교가 무의미하므로 오류로 막는다.
"""
import hashlib
import json
import threading
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

import numpy as np

from app.embed.base import Embedder


class IndexMismatch(RuntimeError):
    """색인을 만든 임베딩 모델·차원이 지금 설정과 다르다 — 재색인 필요."""


class IndexConflict(RuntimeError):
    """같은 청크 ID·프로필에 다른 내용이 이미 있다. 청크는 불변이라 덮어쓰지 않는다 (VDB 설계 v0.3 §3)."""


# 임베딩 입력 형식 버전. 바꾸면 프로필이 달라져 전체 재색인한다
EMBED_INPUT_FORMAT = "l2-nfc-heading-body-v1"


def profile_id(embedder: Embedder) -> str:
    """embedding_profile_id: 모델·차원·정규화·입력 형식을 고정한 키. 같은 프로필끼리만 벡터를 비교한다."""
    return f"{embedder.model_id}:d{embedder.dim}:{EMBED_INPUT_FORMAT}"


class VectorStore(Protocol):
    model_id: str
    dim: int

    def search(self, query: list[float], allowed: set[str], top_k: int) -> list[tuple[str, float]]:
        """allowed(chunk_id 집합) 안에서 코사인 유사도 상위 top_k (chunk_id, score)."""
        ...


def chunk_text(s: dict) -> str:
    """임베딩할 텍스트. 제목이 조항의 주제를 잘 요약하므로 함께 넣는다.
    NFC는 임베딩 입력에만 적용한다 — 원문·offset은 저장된 원문 기준 (VDB 설계 v0.3 §7)."""
    return unicodedata.normalize("NFC", f"{s.get('heading') or ''}\n{s['text']}")


def text_hash(t: str) -> str:
    """embeddingInputSha256 = DB text_hash. 실제 모델 입력과 같은 문자열의 SHA-256 (소문자 hex 64자)."""
    return hashlib.sha256(t.encode("utf-8")).hexdigest()


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


# ---- pgvector (weave_vector) ----
MIGRATION = Path(__file__).resolve().parents[2] / "db" / "vector" / "V1__chunk_embedding.sql"


class PgVectorStore:
    """vector-db의 chunk_embedding. 같은 프로필 행만 비교하고, 허용 청크 ID 밖은 후보에도 오르지 않는다.
    문서·프로젝트 UUID는 지금 샘플 이름에서 만든다 (실제 업무 UUID는 색인 요청에서 받는다)."""

    def __init__(self, url: str, profile: str, dim: int):
        import psycopg
        from pgvector.psycopg import register_vector
        self.model_id, self.dim = profile, dim
        self._conn = psycopg.connect(url, autocommit=True)
        self._lock = threading.Lock()                    # Agent2가 항목별로 스레드를 쓴다
        try:
            register_vector(self._conn)
        except psycopg.ProgrammingError:                 # 확장 없음: migrate() 후 다시 등록
            self._registered = False
        else:
            self._registered = True

    def migrate(self) -> None:
        from pgvector.psycopg import register_vector
        with self._lock:
            self._conn.execute(MIGRATION.read_text(encoding="utf-8"))
            if not self._registered:
                register_vector(self._conn)
                self._registered = True

    def _rows(self, sql: str, params: tuple) -> list[tuple]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def count(self) -> int:
        return self._rows("SELECT count(*) FROM public.chunk_embedding WHERE embedding_profile_id = %s",
                          (self.model_id,))[0][0]

    def check(self, embedder: Embedder) -> None:
        if (profile_id(embedder), embedder.dim) != (self.model_id, self.dim):
            raise IndexMismatch(f"store={self.model_id} ≠ embedder={profile_id(embedder)}")
        if self.count() == 0:
            raise IndexMismatch(f"vector-db에 {self.model_id} 색인이 없음 — python -m app.kb.build_index --store pgvector")

    def search(self, query: list[float], allowed: set[str], top_k: int) -> list[tuple[str, float]]:
        if not allowed:
            return []
        q = np.asarray(query, dtype=np.float32)
        rows = self._rows(
            "SELECT document_chunk_id::text, 1 - (embedding <=> %s) AS score FROM public.chunk_embedding"
            " WHERE embedding_profile_id = %s AND document_chunk_id = ANY(%s::uuid[])"
            " ORDER BY embedding <=> %s, document_chunk_id LIMIT %s",
            (q, self.model_id, sorted(allowed), q, top_k))
        return [(cid, float(score)) for cid, score in rows]

    def existing(self, chunk_ids: list[str]) -> dict[str, tuple]:
        rows = self._rows(
            "SELECT document_chunk_id::text, document_version_id::text, project_id::text, text_hash, extraction_version"
            " FROM public.chunk_embedding WHERE embedding_profile_id = %s AND document_chunk_id = ANY(%s::uuid[])",
            (self.model_id, chunk_ids))
        return {r[0]: r[1:] for r in rows}

    def delete_documents(self, document_version_ids: list[str]) -> int:
        """문서 버전의 벡터를 모든 임베딩 프로필에서 지운다. 로컬 테스트 문서 정리용."""
        if not document_version_ids:
            return 0
        with self._lock, self._conn.transaction():
            cur = self._conn.execute("DELETE FROM public.chunk_embedding WHERE document_version_id = ANY(%s::uuid[])",
                                     (sorted(document_version_ids),))
            return cur.rowcount

    def insert(self, rows: list[tuple]) -> None:
        """(chunk_id, document_version_id, project_id, vector, text_hash, extraction_version). 이미 있으면 건드리지 않는다."""
        with self._lock, self._conn.transaction():
            with self._conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO public.chunk_embedding (document_chunk_id, embedding_profile_id, document_version_id,"
                    " project_id, embedding, text_hash, extraction_version) VALUES (%s, %s, %s, %s, %s, %s, %s)"
                    " ON CONFLICT (document_chunk_id, embedding_profile_id) DO NOTHING",
                    [(c, self.model_id, d, p, np.asarray(v, dtype=np.float32), h, x) for c, d, p, v, h, x in rows])


def build_pg_index(embedder: Embedder, sections: dict, store: PgVectorStore, scope) -> dict:
    """섹션을 pgvector에 색인한다. 같은 ID·같은 내용은 재사용, 같은 ID·다른 내용은 IndexConflict (덮어쓰지 않음).
    scope(section) → (document_version_id, project_id) UUID 문자열."""
    secs = list(sections.values())
    texts = [chunk_text(s) for s in secs]
    want = {s["chunk_id"]: (*scope(s), text_hash(t), s["extraction_version"]) for s, t in zip(secs, texts)}
    have = store.existing(list(want))
    bad = [c for c, row in have.items() if row != want[c]]
    if bad:
        raise IndexConflict(f"{len(bad)}개 청크가 같은 ID·다른 내용 (예: {bad[0]}) — 추출 버전을 올려 새 ID로 색인할 것")
    todo = [i for i, s in enumerate(secs) if s["chunk_id"] not in have]
    if todo:
        vectors = embedder.embed([texts[i] for i in todo])
        store.insert([(secs[i]["chunk_id"], *want[secs[i]["chunk_id"]][:2], v, *want[secs[i]["chunk_id"]][2:])
                      for i, v in zip(todo, vectors)])
    return {"chunks": len(secs), "embedded": len(todo), "reused": len(secs) - len(todo),
            "profile": store.model_id, "store": "pgvector"}


def open_pg_store(settings, embedder: Embedder) -> PgVectorStore:
    from app.embed.base import EmbedConfigError
    if not settings.vector_db_url:
        raise EmbedConfigError("VECTOR_STORE=pgvector에는 VECTOR_DB_URL이 필요함")
    return PgVectorStore(settings.vector_db_url, profile_id(embedder), embedder.dim)


def sample_scope(s: dict) -> tuple[str, str]:
    """샘플 데이터의 (document_version_id, project_id) UUID. 실제 연동에서는 색인 요청의 UUID를 쓴다."""
    from app.kb.loader import documents
    from app.storage.erd import to_uuid
    return to_uuid("document_version", s["doc_id"]), to_uuid("project", documents()[s["doc_id"]]["project_id"])
