# Thursday API and worker. One image, two commands — they share every dependency, and a
# second image would only add a way for them to drift.
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*

# Dependency layer first, so a source change does not reinstall the world.
COPY pyproject.toml README.md ./
COPY packages/ ./packages/
COPY services/ ./services/
COPY apps/ ./apps/
COPY database/ ./database/
COPY alembic.ini settings.yaml ./

RUN pip install --no-cache-dir -e ".[postgres,redis]"

# Run as a non-root user: this process holds the owner's credentials and can act on their
# machines, so it gets no more privilege than it needs.
RUN useradd --create-home --uid 10001 thursday \
    && mkdir -p /data/vault /app/var \
    && chown -R thursday:thursday /data /app/var
USER thursday

ENV THURSDAY_OBSIDIAN_VAULT=/data/vault

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/api/v1/health || exit 1

CMD ["python", "-m", "apps.server", "--host", "0.0.0.0", "--port", "8000"]
