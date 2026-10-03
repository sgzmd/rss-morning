# syntax=docker/dockerfile:1
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential \
        libxml2-dev \
        libxslt1-dev \
        libffi-dev && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ca-certificates \
        libxml2 \
        libxslt1.1 \
        libffi8 && \
    rm -rf /var/lib/apt/lists/* && \
    groupadd -g 1000 appuser && \
    useradd -u 1000 -g appuser -s /sbin/nologin -M appuser

COPY --from=builder /install /usr/local
COPY --chown=appuser:appuser . .

USER 1000:1000

ENTRYPOINT ["python", "main.py"]
