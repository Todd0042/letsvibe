FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/home/letsvibe

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -u 1000 -s /bin/bash letsvibe

WORKDIR /app

COPY pyproject.toml README.md ./
COPY letsvibe ./letsvibe

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir . \
    && mkdir -p /app/data /backups \
    && chown -R 1000:1000 /app /backups /home/letsvibe

USER 1000:1000

CMD ["uvicorn", "letsvibe.web.server:app", "--host", "127.0.0.1", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*", "--no-access-log"]
