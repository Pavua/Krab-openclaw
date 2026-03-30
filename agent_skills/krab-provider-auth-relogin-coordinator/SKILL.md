---
name: krab-provider-auth-relogin-coordinator
description: "Координировать provider auth, OAuth recovery и relogin-процессы для проекта `/Users/pablito/Antigravity_AGENTS/Краб` между `pablito`, `USER2`, `USER3` и account-local runtime слоями, не смешивая `auth-profiles.json`, local tokens, browser state и repo-level truth. Использовать, когда истёк OAuth, требуется relogin OpenAI/Gemini/Qwen/Telegram, owner panel показывает auth degradation или нужен безопасный recovery flow без ручной путаницы."
---

# Krab Provider Auth Relogin Coordinator

Используй этот навык, когда проблема именно в auth/relogin слое, а не в коде бизнес-логики.

## Основные точки входа

- `Login OpenAI Codex OAuth.command`
- `Login Gemini CLI OAuth.command`
- `Login Qwen Portal OAuth.command`
- `telegram_relogin.command`
- `/Users/pablito/Antigravity_AGENTS/Краб/scripts/safe_gemini_oauth.py`
- `/Users/pablito/Antigravity_AGENTS/Краб/scripts/sync_gemini_cli_oauth.py`
- `/Users/pablito/Antigravity_AGENTS/Краб/scripts/openclaw_account_bootstrap.py`

## Рабочий цикл

1. Сначала определи, какой именно auth-layer деградировал:
   - OpenClaw provider auth;
   - Gemini CLI OAuth;
   - OpenAI Codex local auth;
   - Telegram session relogin;
   - browser-mediated auth flow.
2. Проверь, это account-local проблема или общий кодовый дефект.
3. Не копируй `auth-profiles.json` между учётками как shortcut.
4. Предпочитай штатный login/recovery flow над ручной правкой файлов.
5. После relogin зафиксируй:
   - какая учётка;
   - какой provider;
   - что было до;
   - какой state подтвердился после.

## Красные флаги

- Не переносить local auth state с одной учётки на другую как “готовое решение”.
- Не править secrets вручную, если есть штатная `.command` или CLI recovery точка.
- Не объявлять auth repaired, пока нет подтверждения через runtime status или smoke.

## Рекомендуемые связки с другими skills

- `krab-multi-account-dev-coordinator`, если auth issue всплыл на helper-учётке.
- `krab-openclaw-auth-models-keeper`, если recovery упирается в registry/models truth.
- `krab-live-acceptance-brief-writer`, если после relogin нужен короткий truthful note.

## Ресурсы

- Карта provider login/recovery flows: `references/provider-login-matrix.md`
- Шаблон короткой relogin-note: `assets/relogin-note-template.md`
