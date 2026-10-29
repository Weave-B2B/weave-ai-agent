"""② 임베딩·하이브리드 검색 테스트 (API 호출 없음). 색인 생성·재사용, 모델 불일치 차단, 기준선 필터, RRF 결합, provider 요청 형식."""
import json

import httpx
import pytest

from app.core.config import settings
from app.embed import EmbedConfigError, build_embedder
from app.embed.providers import BedrockEmbedder, FakeEmbedder, OpenAIEmbedder
from app.kb.loader import documents, sections
from app.kb.vector_store import IndexMismatch, LocalVectorStore, build_index
from app.tools import doc_tools
from app.tools.doc_tools import search_docs, vector_backend

BL = "P-COM-101:BL-2"


@pytest.fixture
def hybrid(tmp_path, monkeypatch):
    """fake 임베딩으로 색인을 만들고 하이브리드 검색을 켠다. 끝나면 캐시를 비운다."""
    path = tmp_path / "idx.json"
    monkeypatch.setattr(settings, "embed_provider", "fake")
    monkeypatch.setattr(settings, "embed_dim", 64)
    monkeypatch.setattr(settings, "embed_index_path", path)
    monkeypatch.setattr(settings, "retrieval_mode", "hybrid")
    build_index(FakeEmbedder(64), sections(), path)
    vector_backend.cache_clear()
    doc_tools._query_vector.cache_clear()
    yield path
    vector_backend.cache_clear()
    doc_tools._query_vector.cache_clear()


def test_index_build_reuses_unchanged_chunks(tmp_path):
    path = tmp_path / "idx.json"
    first = build_index(FakeEmbedder(32), sections(), path)
    again = build_index(FakeEmbedder(32), sections(), path)
    assert first["embedded"] == len(sections()) and again["embedded"] == 0 and again["reused"] == len(sections())
    other = build_index(FakeEmbedder(16), sections(), path)        # 모델(차원)이 바뀌면 전부 다시
    assert other["embedded"] == len(sections())


def test_index_model_mismatch_is_refused(hybrid, monkeypatch):
    monkeypatch.setattr(settings, "embed_dim", 32)                 # 색인은 64차원
    vector_backend.cache_clear()
    with pytest.raises(IndexMismatch):
        vector_backend()


def test_hybrid_results_stay_inside_baseline(hybrid):
    allowed = {d for d in documents() if d.startswith(("CTR-A", "SOW-A", "FSP-A", "QUO-A", "CO-A"))}
    res = search_docs("구매 후기 사진 업로드", BL, top_k=10)
    assert res and all(r["doc_id"] in allowed for r in res)
    assert {r["via"] for r in res} <= {"lexical", "vector", "both"}


def test_vector_only_hit_is_fused_in(hybrid, monkeypatch):
    """글자가 하나도 안 겹치는 조항이라도 벡터 쪽 상위면 결과에 들어온다 (동의어 대응의 핵심)."""
    target = next(s for (d, _), s in sections().items() if d == "CO-A-001-v1.0")
    _, store = vector_backend()
    monkeypatch.setattr(store, "search", lambda q, allowed, top_k: [(target["chunk_id"], 0.99)])
    res = search_docs("ㅋㅋ", BL, top_k=5)                         # 어휘 점수 0인 질의
    assert [(r["doc_id"], r["section_id"], r["via"]) for r in res][:1] == [(target["doc_id"], target["section_id"], "vector")]


def test_vector_search_filters_other_projects(hybrid):
    store = LocalVectorStore(hybrid)
    allowed = {s["chunk_id"] for (d, _), s in sections().items() if d.startswith("FSP-B")}
    hits = store.search(FakeEmbedder(64).embed(["주문"])[0], allowed, top_k=50)
    assert hits and all(cid in allowed for cid, _ in hits)


def test_lexical_mode_ignores_embeddings(hybrid, monkeypatch):
    monkeypatch.setattr(settings, "retrieval_mode", "lexical")
    vector_backend.cache_clear()
    assert vector_backend() is None
    assert all(r["via"] == "lexical" for r in search_docs("리뷰", BL))


def test_openai_embedder_request_and_order():
    seen = {}

    def handler(req: httpx.Request):
        seen["auth"] = req.headers["authorization"]
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"data": [{"index": 1, "embedding": [0, 2]}, {"index": 0, "embedding": [3, 4]}]})
    emb = OpenAIEmbedder(base_url="https://x/v1", api_key="k", model="text-embedding-3-small", dim=2,
                         client=httpx.Client(transport=httpx.MockTransport(handler)))
    out = emb.embed(["a", "b"])
    assert seen["auth"] == "Bearer k" and seen["body"]["dimensions"] == 2 and seen["body"]["input"] == ["a", "b"]
    assert out == [[0.6, 0.8], [0.0, 1.0]]                      # index 순서 + 정규화
    assert emb.model_id == "openai:text-embedding-3-small"


def test_bedrock_titan_request():
    class Body:
        def read(self):
            return json.dumps({"embedding": [3, 4]})

    class Client:
        def invoke_model(self, **kw):
            self.kw = kw
            return {"body": Body()}
    c = Client()
    emb = BedrockEmbedder(model="amazon.titan-embed-text-v2:0", dim=1024, region="ap-northeast-2", client=c)
    assert emb.embed(["리뷰"]) == [[0.6, 0.8]]
    assert json.loads(c.kw["body"]) == {"inputText": "리뷰", "dimensions": 1024, "normalize": True}
    with pytest.raises(ValueError):
        BedrockEmbedder(model="cohere.embed-multilingual-v3", dim=1024, region="x", client=c)


def test_factory_requires_settings(monkeypatch):
    monkeypatch.setattr(settings, "embed_provider", "openai")
    monkeypatch.setattr(settings, "embed_model", "")
    with pytest.raises(EmbedConfigError):
        build_embedder(settings)
    monkeypatch.setattr(settings, "embed_provider", "nope")
    with pytest.raises(EmbedConfigError):
        build_embedder(settings)
    monkeypatch.setattr(settings, "embed_provider", "none")
    assert build_embedder(settings) is None
