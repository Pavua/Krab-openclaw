"""
Аварийная заглушка для legacy Gemini OAuth скрипта под старый `google-antigravity`.

Зачем оставляем файл:
- чтобы сразу ловить попытки запуска старого обходного flow;
- чтобы не хранить в репозитории client secrets и не маскироваться под чужой client;
- чтобы перенаправить пользователя в официальный путь OpenClaw `google-gemini-cli`.
"""

from __future__ import annotations

import sys


def main() -> int:
    """Печатает причину блокировки и команду штатного relogin."""
    print("Этот скрипт отключён как небезопасный legacy-flow.")
    print("")
    print("Что в нём было не так:")
    print("- зашитый OAuth client secret;")
    print("- ручная запись токенов в auth-profiles.json;")
    print("- привязка к удалённому provider `google-antigravity`.")
    print("")
    print("Актуальный OpenClaw path:")
    print("1. openclaw plugins enable google-gemini-cli-auth")
    print("2. openclaw models auth login --provider google-gemini-cli --set-default")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
