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
CMD ["sh", "-c", "alembic upgrade head && uvicorn cip.api.main:app --host 0.0.0.0 --port 8000"]
