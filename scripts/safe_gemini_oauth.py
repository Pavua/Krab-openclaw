"""
Аварийная заглушка для устаревшего самодельного Gemini OAuth flow.

Зачем оставляем файл:
- чтобы старые ссылки/привычки не ломались внезапным `No such file`;
- чтобы fail-closed остановить опасный сценарий ручной записи OAuth токенов;
- чтобы сразу направить оператора в официальный OpenClaw flow.

Связь с проектом:
- актуальный путь для Gemini OAuth в установленном OpenClaw 2026.3.8 —
  это `google-gemini-cli`, а не legacy `google-antigravity`;
- этот файл намеренно больше ничего не пишет в `auth-profiles.json`
  и не хранит client secrets внутри репозитория.
"""

from __future__ import annotations

import sys


def main() -> int:
    """Останавливает legacy flow и печатает безопасный путь миграции."""
    print("Этот legacy-скрипт отключён из соображений безопасности.")
    print("")
    print("Почему:")
    print("- старый flow обходил официальный OpenClaw auth pipeline;")
    print("- самодельные OAuth-скрипты вручную писали access/refresh токены;")
    print("- provider `google-antigravity` удалён из OpenClaw 2026.3.8.")
    print("")
    print("Используй официальный путь:")
    print("1. openclaw plugins enable google-gemini-cli-auth")
    print("2. openclaw models auth login --provider google-gemini-cli --set-default")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
