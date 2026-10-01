FROM python:3.11-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY backend/pyproject.toml ./
COPY backend/cip ./cip
# Headless Chromium for JavaScript-heavy sites (CIP_BROWSER_RENDERING). Build with
# --build-arg WITH_BROWSER=0 for a smaller image that crawls with plain HTTP only.
ARG WITH_BROWSER=1
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN if [ "$WITH_BROWSER" = "1" ]; then \
      pip install --no-cache-dir ".[browser]" && python -m playwright install --with-deps chromium \
      && chmod -R a+rx /ms-playwright; \
    else pip install --no-cache-dir .; fi
COPY backend/alembic.ini ./
COPY backend/migrations ./migrations
RUN useradd --create-home app && mkdir -p /app/storage && chown app /app/storage
USER app
EXPOSE 8000
# Single worker: analyses run in-process (see docs/deployment.md). Proxy headers are trusted only from
# private networks (the nginx container), so client IPs used for rate limiting and audit are real.
CMD ["sh", "-c", "alembic upgrade head && uvicorn cip.api.main:app --host 0.0.0.0 --port 8000 --workers 1 --proxy-headers --forwarded-allow-ips '127.0.0.1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16'"]
