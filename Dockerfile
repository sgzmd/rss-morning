FROM python:3.14-slim AS base

ARG QUERIES_FILE=queries.txt
ARG OPENAI_API_KEY

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    QUERIES_FILE=${QUERIES_FILE}

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    build-essential \
    libxml2-dev \
    libxslt1-dev \
    libffi-dev && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .

RUN : "${OPENAI_API_KEY:?OPENAI_API_KEY build arg required}" && \
    if [ -f "$QUERIES_FILE" ]; then \
    OPENAI_API_KEY="${OPENAI_API_KEY}" python -m rss_morning.prefilter_cli --output query_embeddings.json --queries-file "$QUERIES_FILE"; \
    else \
    OPENAI_API_KEY="${OPENAI_API_KEY}" python -m rss_morning.prefilter_cli --output query_embeddings.json --queries-file queries.example.txt; \
    fi

COPY query_embeddings.json .

ENTRYPOINT ["python", "main.py"]
