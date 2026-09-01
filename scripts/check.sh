#!/usr/bin/env bash
# Everything CI runs, locally. Run this before pushing.
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
source .venv/bin/activate

echo "→ lint";      ruff check .
echo "→ format";    ruff format --check .
echo "→ types";     mypy packages services || echo "  (mypy findings are advisory for now)"
echo "→ tests";     pytest -q
echo "→ migrations"; alembic upgrade head && alembic check
echo "all green."
