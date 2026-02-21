#!/bin/zsh
# Быстрая проверка совместимости web-прототипа с боевым index.html.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

python3 scripts/validate_web_prototype_compat.py "$@"

