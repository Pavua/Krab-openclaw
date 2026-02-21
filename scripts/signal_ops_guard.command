#!/bin/zsh
# Быстрый запуск Signal Ops Guard (one-shot или daemon).

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

python3 scripts/signal_ops_guard.py "$@"
