# syntax=docker/dockerfile:1.7

FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip setuptools wheel

# Keep downloaded Python wheels in the BuildKit cache.  Dependency installation
# is then reused across source-only rebuilds and after a normal git pull.
RUN --mount=type=cache,id=newscrawler-pip,target=/root/.cache/pip \
    /opt/venv/bin/pip install -r requirements.txt

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    # Chromium runtime deps for Playwright Google-dork SERP scraping
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libdbus-1-3 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libpango-1.0-0 \
    libcairo2 \
    fonts-liberation \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv

# Playwright stores Chromium in a persistent BuildKit cache, then copies the
# browser into the image.  The first build still downloads Chromium; later
# builds only copy the cached files instead of downloading them again.
RUN --mount=type=cache,id=newscrawler-playwright,target=/root/.cache/ms-playwright \
    PLAYWRIGHT_BROWSERS_PATH=/root/.cache/ms-playwright \
    /opt/venv/bin/playwright install chromium \
    && mkdir -p /ms-playwright \
    && cp -a /root/.cache/ms-playwright/. /ms-playwright/

COPY . .

EXPOSE 8000

CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000"]
