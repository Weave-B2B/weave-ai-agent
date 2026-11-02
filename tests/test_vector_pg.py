"""pgvector 저장소. 규칙 테스트는 DB 없이, 저장·검색은 VECTOR_TEST_DB_URL이 있을 때만 실제 vector-db로 돈다."""
import os
import uuid

import pytest

from app.embed.providers import FakeEmbedder
from app.kb.loader import CHUNK_NS, chunk_uuid, sections
from app.kb.vector_store import (EMBED_INPUT_FORMAT, IndexConflict, IndexMismatch, chunk_text, profile_id,
                                 sample_scope, text_hash)

PG_URL = os.environ.get("VECTOR_TEST_DB_URL")


def test_chunk_uuid_matches_design_appendix():
    # VDB 설계 v0.3 부록 예시 — Java 구현과 교차 확인할 고정 값
    assert chunk_uuid("33333333-3333-4333-8333-333333333333", "S-001", "pdf-layout-v1-chunk-v1") \
        == "2fcf3a71-deb9-5342-8d4e-ab6d93b1334d"
    assert CHUNK_NS == uuid.UUID("6f1d3c52-7a0e-4b8e-9d2a-2a7f0c0e5a11")


def test_chunk_uuid_canonicalizes_document_uuid():
    upper = "33333333-3333-4333-8333-333333333333".upper()
    assert chunk_uuid(upper, "S-001", "v1") == chunk_uuid(upper.lower(), "S-001", "v1")
    assert chunk_uuid("{33333333333343338333333333333333}", "S-001", "v1") == chunk_uuid(upper.lower(), "S-001", "v1")
    with pytest.raises(ValueError):
        chunk_uuid(upper, "S:001", "v1")


def test_embedding_input_hash_matches_design_appendix():
    s = {"heading": "변경 범위", "text": "리뷰 기능은 별도 합의 대상으로 한다."}
    assert text_hash(chunk_text(s)) == "49813fce2f49d89ed9eb3143e522856e828a5ad2699efcddb6e94cc4a898445f"
    assert len(text_hash("x")) == 64
    assert chunk_text({"heading": None, "text": "본문"}) == "\n본문"
    assert chunk_text({"heading": "", "text": "é"}) == "\né"          # NFC는 임베딩 입력에만


def test_profile_id_fixes_model_dim_and_input_format():
    assert profile_id(FakeEmbedder(64)) == f"fake:bigram-hash-64:d64:{EMBED_INPUT_FORMAT}"


@pytest.fixture
def pg_store():
    if not PG_URL:
        pytest.skip("VECTOR_TEST_DB_URL 없음 — 실제 vector-db 테스트 생략")
    from app.kb.vector_store import PgVectorStore
    emb = FakeEmbedder(1024)
    store = PgVectorStore(PG_URL, profile_id(emb) + f":test-{uuid.uuid4().hex[:8]}", 1024)   # 테스트마다 다른 프로필
    store.migrate()
    yield emb, store
    store._conn.execute("DELETE FROM public.chunk_embedding WHERE embedding_profile_id = %s", (store.model_id,))


def test_pg_index_reuses_rows_and_searches_only_allowed(pg_store):
    from app.kb.vector_store import build_pg_index
    emb, store = pg_store
    secs = dict(list(sections().items())[:12])
    first = build_pg_index(emb, secs, store, sample_scope)
    assert (first["embedded"], first["reused"]) == (12, 0)
    again = build_pg_index(emb, secs, store, sample_scope)
    assert (again["embedded"], again["reused"]) == (0, 12)
    target = next(iter(secs.values()))
    q = emb.embed([chunk_text(target)])[0]
    allowed = {s["chunk_id"] for s in secs.values()}
    hits = store.search(q, allowed, 3)
    assert hits[0][0] == target["chunk_id"] and hits[0][1] > 0.99
    other = allowed - {target["chunk_id"]}
    assert target["chunk_id"] not in {c for c, _ in store.search(q, other, 20)}
    assert store.search(q, set(), 5) == []


def test_pg_index_refuses_same_id_with_different_content(pg_store):
    from app.kb.vector_store import build_pg_index
    emb, store = pg_store
    key, s = next(iter(sections().items()))
    build_pg_index(emb, {key: s}, store, sample_scope)
    with pytest.raises(IndexConflict):
        build_pg_index(emb, {key: {**s, "text": s["text"] + " 변경"}}, store, sample_scope)


def test_pg_store_rejects_other_profile_and_empty_index(pg_store):
    from app.kb.vector_store import PgVectorStore
    emb, store = pg_store
    with pytest.raises(IndexMismatch):
        store.check(emb)                                   # 저장소 프로필에 테스트 접미어가 붙어 다르다

    class Unindexed:
        model_id, dim = f"fake:unindexed-{uuid.uuid4().hex[:8]}", 1024
    empty = PgVectorStore(PG_URL, profile_id(Unindexed), 1024)
    with pytest.raises(IndexMismatch, match="색인이 없음"):
        empty.check(Unindexed)
