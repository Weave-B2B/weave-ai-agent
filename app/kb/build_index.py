"""청크 임베딩 색인 생성. 사용: python -m app.kb.build_index

EMBED_PROVIDER·EMBED_MODEL·EMBED_DIM으로 모델을 고른다. 문서·추출 버전·모델이 바뀌었을 때 다시 실행한다
(바뀌지 않은 청크는 이전 벡터를 재사용한다).
"""
import json
import sys

from app.core.config import settings
from app.embed import build_embedder
from app.kb.loader import sections
from app.kb.vector_store import build_index


def main() -> int:
    emb = build_embedder(settings)
    if emb is None:
        print("EMBED_PROVIDER=none — 색인할 임베딩 모델이 없음", file=sys.stderr)
        return 1
    print(json.dumps(build_index(emb, sections(), settings.embed_index_path), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
