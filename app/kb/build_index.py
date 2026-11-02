"""청크 임베딩 색인 생성.

    python -m app.kb.build_index                              # 로컬 JSON (EMBED_INDEX_PATH)
    python -m app.kb.build_index --store pgvector --migrate   # vector-db (VECTOR_DB_URL), 테이블이 없으면 만든다

EMBED_PROVIDER·EMBED_MODEL·EMBED_DIM으로 모델을 고른다. 문서·추출 버전·모델이 바뀌었을 때 다시 실행한다
(바뀌지 않은 청크는 이전 벡터를 재사용한다).
"""
import argparse
import json
import sys

from app.core.config import settings
from app.embed import build_embedder
from app.kb.loader import sections
from app.kb.vector_store import build_index, build_pg_index, open_pg_store, sample_scope


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--store", choices=("local", "pgvector"), default=settings.vector_store)
    ap.add_argument("--migrate", action="store_true", help="pgvector: db/vector 마이그레이션 SQL 적용")
    args = ap.parse_args(argv)
    emb = build_embedder(settings)
    if emb is None:
        print("EMBED_PROVIDER=none — 색인할 임베딩 모델이 없음", file=sys.stderr)
        return 1
    if args.store == "pgvector":
        store = open_pg_store(settings, emb)
        if args.migrate:
            store.migrate()
        report = build_pg_index(emb, sections(), store, sample_scope)
    else:
        report = build_index(emb, sections(), settings.embed_index_path)
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
