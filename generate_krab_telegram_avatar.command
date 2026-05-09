#!/bin/zsh
: <<'DOC'
Это macOS-лаунчер для сборки анимированного Telegram-аватара Краба.
Он нужен, чтобы пересобрать MP4/WebM одним двойным кликом и сразу получить
готовый файл в artifacts/telegram_avatar/krab_telegram_avatar.mp4.
Лаунчер связан со scripts/generate_krab_telegram_avatar.py и не требует сервера.
DOC

set -euo pipefail

SCRIPT_DIR="${0:A:h}"
cd "$SCRIPT_DIR"

python3 scripts/generate_krab_telegram_avatar.py
open artifacts/telegram_avatar
