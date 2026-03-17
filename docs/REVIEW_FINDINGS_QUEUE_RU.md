# -*- coding: utf-8 -*-
"""
Очередь подтверждённых review-findings для Краба.

Что это:
- короткий backlog реальных багов, подтверждённых code review и/или ручной
  верификацией по коду и тестам;
- используется как checkpoint, если рабочая квота Codex заканчивается раньше,
  чем удаётся закрыть весь блок.

Зачем нужно:
- не потерять уже найденные regressions между диалогами и handoff;
- отделить подтверждённые баги от спорных и ложноположительных замечаний;
- держать приоритеты в одном месте, не перечитывая длинные диалоги.
"""

# Очередь Review Findings

## Уже закрыто

- `codex/review-findings-hardening-clean`
  - `1936a97` `fix: block autoswitch providers with expired auth only`
  - `c164f68` `test: cover expired-only autoswitch provider path`
- `codex/userbot-tech-notices`
  - `7e862de` `fix: avoid drift in tech notice toggles`
- `codex/translator-control-plane-fix`
  - `current_session_id`: выбор самой свежей active session
  - `quick phrases`: сохранение non-`ru/es` языковой пары

## Подтверждено и ждёт фикса

### P1

- `auth_recovery_readiness`: guard для `PermissionError` вокруг `/opt/homebrew/bin/openclaw`
  - Симптом: owner panel / model catalog / post-apply response могут падать в средах,
    где бинарник существует, но недоступен для `stat()`.
  - Файл: `src/core/auth_recovery_readiness.py`

- `openai-safe` не блокируется при auth-failing OpenAI lane
  - Симптом: recovery-профиль может продолжать продвигать `openai/gpt-4o-mini`
    сразу после runtime auth-scope failure.
  - Файл: `scripts/openclaw_model_autoswitch.py`

### P2

- `.command` login launcher теряет ошибку из-за `set -e`
  - Симптом: окно может закрываться раньше, чем пользователь увидит финальный
    статус неудачного login/recovery flow.
  - Файл: `Login Gemini CLI OAuth.command`

## Нужна дополнительная верификация / контекст

- `openclaw_model_compat_probe`: смешение override runtime snapshot и live provider catalog
  - Пока не чиним вслепую: зависит от intended контракта probe.

## Следующий оптимальный порядок

1. `auth_recovery_readiness` guard
2. `openai-safe` runtime auth-failure handling
3. `.command` launcher status-on-failure UX
