# Stage 1: build dependencies
FROM python:3.11-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y \
    build-essential \
    libffi-dev \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

COPY bot/requirements.txt /tmp/requirements.txt
COPY api/requirements.txt /tmp/requirements_api.txt

RUN pip install --no-cache-dir --prefix=/install -r /tmp/requirements.txt && \
    pip install --no-cache-dir --prefix=/install -r /tmp/requirements_api.txt

# Stage 2: runtime - minimal image
FROM python:3.11-slim

LABEL maintainer="crypto-trader"
LABEL description="Crypto Trader Bot with ML"

WORKDIR /app

# Only runtime essentials
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local

COPY . .

RUN mkdir -p /app/data /app/model

ENV PYTHONUNBUFFERED=1

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
