#!/bin/zsh
# Открывает fast-path shared worktree в Finder.

set -euo pipefail

TARGET="/Users/Shared/Antigravity_AGENTS/Краб-active"

if [[ ! -d "$TARGET" ]]; then
  echo "active shared worktree не найден: $TARGET"
  exit 1
fi

open "$TARGET"
