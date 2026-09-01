# A device node in a container — useful for a headless Linux box, and for exercising the
# real WebSocket path in integration tests. A desktop node runs on the host, not here:
# a container cannot open the user's applications.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY pyproject.toml README.md ./
COPY packages/ ./packages/
COPY services/ ./services/
COPY apps/ ./apps/
RUN pip install --no-cache-dir -e .

RUN useradd --create-home --uid 10002 node && mkdir -p /data && chown -R node:node /data
USER node

# The node's allowed roots are explicit. It can touch nothing else, whatever the core asks.
CMD ["python", "-m", "apps.node", "--core", "ws://api:8000/api/v1/device", "--allow-root", "/data"]
