#!/bin/bash
# 🦀 Запуск Краба одной кнопкой (Full Stack)
# Назначение: legacy-shell wrapper, перенаправленный на канонический launcher.

cd "$(dirname "$0")"

exec "./new start_krab.command"
