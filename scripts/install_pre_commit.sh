#!/usr/bin/env bash
# Устанавливает pre-commit hook для auto-format src/ и scripts/ перед commit.
# Wave 55-A: предотвращает CI-ошибки ruff format/lint в PRs.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# --- Проверка/установка pre-commit ---
if ! command -v pre-commit &>/dev/null; then
    echo "[install_pre_commit] pre-commit не найден — устанавливаю через pip..."
    pip install pre-commit
else
    echo "[install_pre_commit] pre-commit уже установлен: $(pre-commit --version)"
fi

# --- Attach hook к git repo ---
echo "[install_pre_commit] Регистрирую hook: pre-commit install"
pre-commit install

# --- Опционально: проверить конфиг ---
echo "[install_pre_commit] Конфиг: .pre-commit-config.yaml"
if [[ -f ".pre-commit-config.yaml" ]]; then
    echo "[install_pre_commit] OK — .pre-commit-config.yaml найден"
else
    echo "[install_pre_commit] WARN — .pre-commit-config.yaml не найден в $REPO_ROOT"
    exit 1
fi

echo ""
echo "[install_pre_commit] Готово. При каждом git commit:"
echo "  1. ruff check --fix src/ scripts/  (auto-fix линт)"
echo "  2. ruff format src/ scripts/       (auto-format)"
echo "  Если файлы изменены — commit отклоняется; re-stage и retry."
