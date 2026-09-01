#!/usr/bin/env bash
# Bring up a working Thursday for development.
#
#   scripts/dev.sh            local: SQLite, in-process bus, no containers
#   scripts/dev.sh stack      the locked stack: Postgres + Redis in Docker
set -euo pipefail

cd "$(dirname "$0")/.."
MODE="${1:-local}"

if [[ ! -d .venv ]]; then
  echo "→ creating the virtualenv"
  uv venv --python 3.12
fi
# shellcheck disable=SC1091
source .venv/bin/activate

echo "→ installing"
uv pip install -q -e ".[dev]"

if [[ "$MODE" == "stack" ]]; then
  echo "→ starting Postgres and Redis"
  docker compose up -d postgres redis
  # Wait for the healthchecks rather than sleeping and hoping.
  until docker compose exec -T postgres pg_isready -U thursday >/dev/null 2>&1; do sleep 1; done
  export THURSDAY_DB_HOST=localhost THURSDAY_DB_USER=thursday THURSDAY_DB_NAME=thursday
  export THURSDAY_DB_PASSWORD="${POSTGRES_PASSWORD:-thursday}"
  export THURSDAY_REDIS_URL="redis://localhost:6379/0"
fi

echo "→ migrating"
alembic upgrade head

echo "→ seeding"
python -m database.seeds

cat <<'MSG'

Ready. Three terminals:

  python -m apps.server                                  core API on :8000
  python -m apps.node --name Office-PC --allow-root ~    one per machine
  python -m apps.cli --remote                            talk to it

Or one, with an embedded core and node:

  python -m apps.cli

MSG
