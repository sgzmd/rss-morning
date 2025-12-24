FROM python:3.12-slim AS base

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

# Pre-download the embedding model
# RUN python -c "from fastembed import TextEmbedding; TextEmbedding(model_name='intfloat/multilingual-e5-large')"

ENV FASTEMBED_CACHE_PATH=/app/data/fastembed_cache

COPY . .

ENTRYPOINT ["python", "main.py"]
