#!/bin/bash
# 🦀 Krab Userbot — Standalone Launcher (macOS)
# Назначение: legacy-start, перенаправленный на актуальный `new start_krab.command`.
# Связи: сначала ищет repo-local launcher, затем sibling launcher уровнем выше,
# чтобы shared repo и внешний one-click вход не расходились между учётками.

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
AG_ROOT="$(cd "$DIR/.." && pwd)"
cd "$DIR"

LOCAL_NEW="$DIR/new start_krab.command"
PARENT_NEW="$AG_ROOT/new start_krab.command"

if [ -x "$LOCAL_NEW" ]; then
    exec "$LOCAL_NEW"
fi

if [ -x "$PARENT_NEW" ]; then
    exec "$PARENT_NEW"
fi

echo "❌ Не найден актуальный launcher start_krab.command."
echo "Проверил:"
echo "  - $LOCAL_NEW"
echo "  - $PARENT_NEW"
exit 1
