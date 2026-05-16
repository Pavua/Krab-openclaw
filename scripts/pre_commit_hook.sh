#!/usr/bin/env bash
# pre-commit hook: ruff check + ruff format + mypy на src/handlers/
# Чтобы пропустить: git commit --no-verify
# Session 53: worktree support — venv resolved via --git-common-dir (main repo)

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
# В worktree venv живёт в main repo (resolve через --git-common-dir)
MAIN_REPO="$(cd "$(dirname "$(git rev-parse --git-common-dir)")" && pwd)"
VENV="${KRAB_VENV_BIN:-$MAIN_REPO/venv/bin}"

# Список закоммиченных .py файлов в src/
STAGED_PY=$(git diff --cached --name-only --diff-filter=ACM | grep -E '^src/.*\.py$' || true)

if [[ -z "$STAGED_PY" ]]; then
    exit 0
fi
    exit 0
fi

echo "[pre-commit] Проверка staged python-файлов..."

# --- ruff check (без автоисправления) ---
echo "[pre-commit] ruff check..."
if ! echo "$STAGED_PY" | xargs "$VENV/ruff" check --no-fix 2>&1; then
    echo ""
    echo "[pre-commit] FAIL: ruff check. Исправь или запусти: venv/bin/ruff check --fix src/"
    exit 1
fi

# --- ruff format (только проверка) ---
echo "[pre-commit] ruff format --check..."
if ! echo "$STAGED_PY" | xargs "$VENV/ruff" format --check 2>&1; then
    echo ""
    echo "[pre-commit] FAIL: ruff format. Запусти: venv/bin/ruff format src/"
    exit 1
fi

# --- mypy только на src/handlers/**/*.py (follow-imports=skip — не тянет тяжёлые deps) ---
HANDLER_PY=$(echo "$STAGED_PY" | grep -E '^src/handlers/.*\.py$' || true)

if [[ -n "$HANDLER_PY" ]]; then
    echo "[pre-commit] mypy src/handlers/..."
    # Non-blocking по умолчанию: pre-existing legacy ошибки типов не должны блокировать.
    # Для strict mode: KRAB_PRECOMMIT_MYPY_STRICT=1 git commit ...
    if ! echo "$HANDLER_PY" | xargs "$VENV/mypy" \
            --ignore-missing-imports \
            --follow-imports=skip \
            --no-error-summary \
            2>&1; then
        if [[ "${KRAB_PRECOMMIT_MYPY_STRICT:-0}" == "1" ]]; then
            echo ""
            echo "[pre-commit] FAIL: mypy нашёл ошибки типов (strict mode)"
            exit 1
        else
            echo ""
            echo "[pre-commit] WARN: mypy нашёл ошибки типов в src/handlers/ (non-blocking; KRAB_PRECOMMIT_MYPY_STRICT=1 чтобы блокировать)"
        fi
    fi
fi

echo "[pre-commit] OK"
exit 0
