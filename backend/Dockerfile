FROM node:22-bookworm-slim@sha256:43ac6c60b8f89723f746e8a92ce91abd5017e627ce1ddfe4238355d3a30b772c AS math-runtime
WORKDIR /math_runtime
COPY math_runtime/package.json math_runtime/package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts

FROM python:3.12.14-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.9.5 /uv /usr/local/bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH=/app/src \
    HF_HOME=/cache/huggingface \
    EASYOCR_MODULE_PATH=/cache/easyocr \
    OMP_NUM_THREADS=2

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-cache

COPY src ./src
COPY --from=math-runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=math-runtime /math_runtime /app/math_runtime
COPY math_runtime/math_speech.mjs /app/math_runtime/math_speech.mjs

RUN groupadd --gid 10001 upraise && useradd --uid 10001 --gid upraise --create-home upraise \
    && mkdir -p /data/ingestion /cache \
    && chown -R upraise:upraise /data /cache

USER upraise

EXPOSE 8000

CMD ["uvicorn", "upraise_ai.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]
