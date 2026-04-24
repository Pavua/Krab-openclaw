#!/bin/bash
# Install Krab git hooks — copies tracked templates from scripts/git_hooks/
# into .git/hooks/ and makes them executable.
#
# Run once after clone / fresh worktree:
#     bash scripts/install_git_hooks.sh
#
# Safe to re-run (idempotent: overwrites existing hooks with tracked copy).

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
SRC_DIR="$REPO_ROOT/scripts/git_hooks"
DST_DIR="$REPO_ROOT/.git/hooks"

if [ ! -d "$SRC_DIR" ]; then
    echo "ERR: template dir missing: $SRC_DIR" >&2
    exit 1
fi
if [ ! -d "$DST_DIR" ]; then
    echo "ERR: .git/hooks dir missing (not inside a git worktree?)" >&2
    exit 1
fi

INSTALLED=0
for hook in "$SRC_DIR"/*; do
    [ -f "$hook" ] || continue
    name=$(basename "$hook")
    dest="$DST_DIR/$name"
    cp "$hook" "$dest"
    chmod +x "$dest"
    echo "installed: $name"
    INSTALLED=$((INSTALLED + 1))
done

# Helper scripts тоже делаем executable (вызываются из hooks).
for helper in \
    "$REPO_ROOT/scripts/sentry_resolve_from_commit.sh"; do
    [ -f "$helper" ] && chmod +x "$helper"
done

echo ""
echo "Done. Hooks installed: $INSTALLED → $DST_DIR"
echo ""
echo "Activate auto-push + Sentry resolve в .env:"
echo "  KRAB_AUTOPUSH=1"
echo "  KRAB_POSTCOMMIT_E2E=0          # 1 для e2e после каждого commit"
echo "  SENTRY_AUTH_TOKEN=sntryu_...   # уже должен быть для alerts"
echo "  SENTRY_ORG_SLUG=<org-slug>"
echo ""
echo "Log: /tmp/krab_post_commit.log"
