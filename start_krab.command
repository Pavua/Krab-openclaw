#!/bin/bash
# 🦀 Krab Userbot — Standalone Launcher (macOS)
# Назначение: legacy-start, перенаправленный на `new start_krab.command`.

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

exec "$DIR/new start_krab.command"
