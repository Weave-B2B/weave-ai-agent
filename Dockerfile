# .python-version과 맞춘다 (변경 시 둘 다 수정)
ARG PYTHON_VERSION=3.11.9

FROM python:${PYTHON_VERSION}-slim

# 로그를 버퍼 없이 바로 출력하고, .pyc 파일을 만들지 않는다
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# poppler-utils: 문서 색인의 PDF 텍스트 추출(pdftotext)에 사용
RUN apt-get update \
    && apt-get install -y --no-install-recommends poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# 코드가 저장소 루트를 기준으로 app·schema·prototype 패키지와 data 폴더를 찾으므로 루트 구조를 그대로 둔다
WORKDIR /app

# 의존성 레이어 캐시: requirements.txt가 바뀌지 않으면 다시 설치하지 않음
COPY requirements.txt ./
RUN pip install -r requirements.txt

# 실행에 필요한 폴더만 복사한다 (.env, tests, docs 등은 넣지 않음)
# prototype: app/main.py가 import하므로 없으면 기동 실패
# eval: 기본 실행 모드(AGENT_MODE=fixture)가 읽는 데이터
# db: 벡터 DB 테이블 생성 SQL / examples: 테스트 화면의 예시 응답
COPY app app
COPY schema schema
COPY prototype prototype
COPY data data
COPY eval eval
COPY db db
COPY examples examples

# root가 아닌 계정으로 실행한다. 실행 중 파일을 쓰는 폴더만 그 계정 소유로 둔다
# .prototype-*: 테스트 화면의 실행 상태(SQLite)·단가표·문서 저장 / data/index: 로컬 임베딩 색인
RUN groupadd --system weave && useradd --system --gid weave weave \
    && mkdir -p .prototype-runs .prototype-rate-cards .prototype-kb data/index \
    && chown -R weave:weave .prototype-runs .prototype-rate-cards .prototype-kb data/index
USER weave

EXPOSE 8000

# 상태 확인: GET /health (로그인 없이 200, {"status":"ok"})
# 설정값은 모두 환경변수로 받는다 (.env 파일은 이미지에 없음)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
