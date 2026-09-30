# Scenario Analytics API
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install dependencies first so this layer is cached between code changes.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY sql ./sql
COPY data ./data

RUN useradd --create-home --uid 1000 appuser
USER appuser

# Hosting platforms (Render, Azure Container Apps, Railway) inject PORT.
ENV PORT=8000
EXPOSE 8000

# Apply the (idempotent) schema, load the sample data on first start only,
# then serve. Fails fast if the database is unreachable so the platform
# restarts the container instead of serving 503s.
CMD ["sh", "-c", "python -m app.cli init-db && python -m app.cli seed && exec uvicorn app.api.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
