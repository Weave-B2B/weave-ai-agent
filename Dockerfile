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

# PDF 파서(docling)가 쓰는 torch는 CPU 전용 휠을 먼저 설치한다.
# 기본 PyPI 휠은 CUDA 라이브러리를 함께 받아 이미지가 수 GB 커진다 (GPU 없이 CPU로만 실행)
RUN pip install --index-url https://download.pytorch.org/whl/cpu torch==2.15.0 torchvision==0.29.2

# 의존성 레이어 캐시: requirements.txt가 바뀌지 않으면 다시 설치하지 않음
COPY requirements.txt ./
RUN pip install -r requirements.txt

# PDF 파서 모델(레이아웃·표 구조)을 빌드 때 받아 둔다. docling은 DOCLING_ARTIFACTS_PATH에서 읽고 실행 중 내려받지 않는다.
# OCR은 쓰지 않는다 (텍스트 층이 없는 쪽은 이미지 전사로 처리, app/indexing/pdf_r2.py)
ENV DOCLING_ARTIFACTS_PATH=/opt/docling-models
RUN docling-tools models download layout tableformer -o ${DOCLING_ARTIFACTS_PATH} \
    && chmod -R a+rX ${DOCLING_ARTIFACTS_PATH}

# 청크 길이 계산(tiktoken cl100k_base) 사전도 빌드 때 받아 둔다. 없으면 실행 중 외부에서 내려받으려 한다
ENV TIKTOKEN_CACHE_DIR=/opt/tiktoken-cache
RUN python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')" \
    && chmod -R a+rX ${TIKTOKEN_CACHE_DIR}

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

# 배포 환경에서 trace exporter를 지정하면 전송 활성화
ENV OTEL_TRACES_EXPORTER=none \
    OTEL_METRICS_EXPORTER=none \
    OTEL_LOGS_EXPORTER=none \
    OTEL_PYTHON_LOG_CORRELATION=true \
    OTEL_PYTHON_LOG_AUTO_INSTRUMENTATION=false

# 상태 확인: GET /health (로그인 없이 200, {"status":"ok"})
# 설정값은 모두 환경변수로 받는다 (.env 파일은 이미지에 없음)
CMD ["opentelemetry-instrument", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--log-config", "app/logging_config.json"]
