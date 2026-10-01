FROM python:3.11-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY backend/pyproject.toml ./
COPY backend/cip ./cip
RUN pip install --no-cache-dir .
COPY backend/alembic.ini ./
COPY backend/migrations ./migrations
RUN useradd --create-home app && mkdir -p /app/storage && chown app /app/storage
USER app
EXPOSE 8000
# Single worker: analyses run in-process (see docs/deployment.md). Proxy headers are trusted only from
# private networks (the nginx container), so client IPs used for rate limiting and audit are real.
CMD ["sh", "-c", "alembic upgrade head && uvicorn cip.api.main:app --host 0.0.0.0 --port 8000 --workers 1 --proxy-headers --forwarded-allow-ips '127.0.0.1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16'"]
