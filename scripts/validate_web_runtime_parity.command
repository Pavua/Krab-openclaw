#!/bin/zsh
# Быстрая проверка JS runtime parity между боевым index.html и прототипом.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

python3 scripts/validate_web_runtime_parity.py "$@"
