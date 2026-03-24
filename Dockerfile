FROM python:3.11-slim

LABEL maintainer="crypto-trader"
LABEL description="Crypto Trader Bot with ML"

WORKDIR /app

RUN apt-get update && apt-get install -y \
    build-essential \
    libffi-dev \
    libssl-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY bot/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY api/requirements.txt /tmp/requirements_api.txt
RUN pip install --no-cache-dir -r /tmp/requirements_api.txt

COPY . .

RUN mkdir -p /app/data /app/model

ENV PYTHONUNBUFFERED=1

CMD ["python", "bot/main.py"]
