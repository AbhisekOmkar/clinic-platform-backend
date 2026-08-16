FROM python:3.11-slim AS builder

WORKDIR /build
RUN pip install --no-cache-dir poetry==2.1.3 && poetry config virtualenvs.create false
COPY pyproject.toml poetry.lock ./
RUN poetry install --only main --no-root

FROM python:3.11-slim

WORKDIR /srv
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY app/ app/
COPY scripts/ scripts/

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
EXPOSE 4226
HEALTHCHECK --interval=30s --timeout=5s CMD curl -f http://localhost:4226/health || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "4226"]
