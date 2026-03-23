#!/bin/bash
# 🛑 Stop Krab 🦀
# Назначение: legacy-точка остановки, делегирующая в актуальный `new Stop Krab.command`.
# Связи: сначала ищет repo-local stop launcher, затем sibling launcher уровнем выше,
# чтобы stop не ломался в shared/multi-account раскладке.

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
AG_ROOT="$(cd "$DIR/.." && pwd)"
cd "$DIR"

LOCAL_NEW="$DIR/new Stop Krab.command"
PARENT_NEW="$AG_ROOT/new Stop Krab.command"

if [ -x "$LOCAL_NEW" ]; then
    exec "$LOCAL_NEW"
fi

if [ -x "$PARENT_NEW" ]; then
    exec "$PARENT_NEW"
fi

echo "❌ Не найден актуальный stop-launcher."
echo "Проверил:"
echo "  - $LOCAL_NEW"
echo "  - $PARENT_NEW"
exit 1
