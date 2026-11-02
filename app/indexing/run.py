"""색인 1건을 직접 실행한다 (SQS 연결 전 개발용). 결과(A-MQ-02 형식 JSON)를 출력한다.

    python -m app.indexing.run request.json                      # S3(S3_ENDPOINT면 LocalStack)
    python -m app.indexing.run request.json --dir /tmp/objects   # <dir>/<bucket>/<key> 로컬 파일

request.json은 백엔드 색인 요청(A-MQ-01) 형식. VECTOR_DB_URL·EMBED_* 설정이 필요하다.
"""
import argparse
import json
import sys
from pathlib import Path

from app.core.config import settings
from app.embed import build_embedder
from app.indexing.messages import IndexRequest
from app.indexing.objects import DirStore, S3Store
from app.indexing.pipeline import index_document
from app.kb.vector_store import open_pg_store


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("request", type=Path)
    ap.add_argument("--dir", type=Path, help="S3 대신 로컬 디렉터리")
    ap.add_argument("--migrate", action="store_true", help="vector-db 테이블이 없으면 만든다")
    args = ap.parse_args(argv)
    req = IndexRequest.model_validate_json(args.request.read_text(encoding="utf-8"))
    emb = build_embedder(settings)
    if emb is None:
        print("EMBED_PROVIDER=none — 임베딩 모델이 없음", file=sys.stderr)
        return 1
    store = open_pg_store(settings, emb)
    if args.migrate:
        store.migrate()
    objects = DirStore(args.dir) if args.dir else S3Store(settings.s3_endpoint or None, settings.s3_region)
    result = index_document(req, objects, emb, store, settings.s3_source_bucket, settings.s3_result_bucket)
    print(json.dumps(result.to_json(), ensure_ascii=False, indent=2))
    return 0 if result.status == "READY" else 2


if __name__ == "__main__":
    sys.exit(main())
