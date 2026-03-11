#!/bin/bash
# 🛑 Stop Krab 🦀
# Назначение: legacy-точка остановки, делегирующая в `new Stop Krab.command`.

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

exec "$DIR/new Stop Krab.command"
