-- weave_vector (weave-infra vector-db, PostgreSQL 18 + pgvector 0.8.x) — Agent2 청크 임베딩
-- 「VDB 청크 임베딩 SQL 초안 v0.3」 §2 벡터 테이블. 작업 장부(ai_ops)는 색인 worker 단계에서 추가한다.
-- 적용: python -m app.kb.build_index --store pgvector --migrate (다시 실행해도 안전)

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS public.chunk_embedding (
    document_chunk_id    UUID         NOT NULL,   -- = document_chunk.document_chunk_id (CN이 같은 UUID로 저장, FK 불가)
    embedding_profile_id VARCHAR(200) NOT NULL,   -- 모델·차원·정규화·입력 형식 (예: openai:text-embedding-3-small:d1024:l2-nfc-heading-body-v1)
    document_version_id  UUID         NOT NULL,
    project_id           UUID         NOT NULL,
    embedding            vector(1024) NOT NULL,   -- 길이 1로 정규화
    text_hash            CHAR(64)     NOT NULL,   -- embeddingInputSha256: 실제 임베딩 입력(NFC 제목+본문)의 SHA-256
    extraction_version   VARCHAR(100) NOT NULL,
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (document_chunk_id, embedding_profile_id),
    CHECK (text_hash ~ '^[0-9a-f]{64}$'),
    CHECK (length(btrim(embedding_profile_id)) > 0),
    CHECK (length(btrim(extraction_version)) > 0),
    CHECK (vector_norm(embedding) > 0)
);

-- 검색 범위 필터. 기본은 정확 검색이며 HNSW는 필터 포함 recall·지연을 검증한 뒤 따로 추가한다 (v0.3 §9)
CREATE INDEX IF NOT EXISTS chunk_embedding_scope_idx
    ON public.chunk_embedding (project_id, embedding_profile_id, document_version_id);
