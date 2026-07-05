FROM python:3.12-slim

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY templates ./templates
COPY static ./static

ENV BOOKVOTE_DATA_DIR=/data
VOLUME ["/data"]

EXPOSE 8000
# --proxy-headers + --forwarded-allow-ips='*': trust X-Forwarded-For/-Proto
# from whoever connects to this port. Safe here because the port is only
# published on 127.0.0.1 (see docker-compose.yml) — only a same-host proxy
# (nginx/Caddy) can reach it, never the public internet directly. Without
# this, every visitor looks like the same IP to the anti-bot/rate-limit
# logic once you're behind any reverse proxy.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
