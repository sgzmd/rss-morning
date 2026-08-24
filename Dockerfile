# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential \
        libffi-dev \
        libxml2-dev \
        libxslt1-dev && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt /wheels/requirements.txt
RUN python -m pip wheel --wheel-dir /wheels -r /wheels/requirements.txt


FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.source="https://github.com/sgzmd/rss-morning" \
      org.opencontainers.image.title="RSS Morning" \
      org.opencontainers.image.description="Bounded RSS digest generation"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    FASTEMBED_CACHE_PATH=/app/data/fastembed_cache \
    TIKTOKEN_CACHE_DIR=/app/data/tiktoken_cache \
    DB_PATH=/app/data/db

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates libgomp1 && \
    rm -rf /var/lib/apt/lists/*

RUN --mount=type=bind,from=builder,source=/wheels,target=/wheels \
    python -m pip install --no-index --find-links=/wheels -r /wheels/requirements.txt

RUN mkdir -p "$FASTEMBED_CACHE_PATH" "$TIKTOKEN_CACHE_DIR" "$DB_PATH" && \
    python -c 'import tiktoken; tiktoken.get_encoding("cl100k_base")' && \
    useradd --create-home --uid 1000 appuser && \
    chown -R appuser:appuser /app/data && \
    chmod -R a+rX "$TIKTOKEN_CACHE_DIR"

COPY --chown=appuser:appuser main.py ./main.py
COPY --chown=appuser:appuser rss_morning ./rss_morning
COPY --chown=appuser:appuser queries.example.txt ./queries.example.txt
COPY --chown=appuser:appuser scripts/container_smoke.py ./scripts/container_smoke.py

USER appuser
ENTRYPOINT ["python", "main.py"]
