# Prompt Для Нового Чата — 21.03.2026

Продолжаем проект `Краб` из репозитория `/Users/pablito/Antigravity_AGENTS/Краб`.
Используй приложенные документы как source of truth и не опирайся на старые пересказы.

Сначала прочитай:

1. `/Users/pablito/Antigravity_AGENTS/Краб/docs/2026-03-21/HANDOFF_PORTABLE_RU.md`
2. `/Users/pablito/Antigravity_AGENTS/Краб/docs/2026-03-21/KNOWN_AND_DISCUSSED_RU.md`
3. `/Users/pablito/Antigravity_AGENTS/Краб/docs/handoff/MASTER_PLAN_SOURCE_OF_TRUTH.md`
4. `/Users/pablito/Antigravity_AGENTS/Краб/docs/MASTER_PLAN_VNEXT_RU.md`
5. `/Users/pablito/Antigravity_AGENTS/Краб/docs/LM_STUDIO_MCP_SETUP_RU.md`

Текущая truthful картина:

- web panel `:8080` поднята
- OpenClaw gateway `:18789` поднят
- Voice Gateway `:8090` поднят
- Krab Ear поднят
- warmup truthful: `google-gemini-cli/gemini-3.1-pro-preview`, `active_tier=paid`
- Codex MCP на USER2 usable
- browser truth и owner/debug Chrome path разведены
- live progress проекта около `91%`
- baseline master-plan около `31%` и не равен live progress

Ключевой confirmed blocker:

- ordinary Chrome attach к default profile на Chrome `146.0.7680.154`
  заблокирован политикой Chrome
- helper-log содержит:
  `DevTools remote debugging requires a non-default data directory`
- `127.0.0.1:9222` слушает, но:
  - `/json/version` даёт `404`
  - websocket browser endpoint уходит в `TimeoutError`

Поэтому не повторяй старую гипотезу:

- дело не в permission prompt
- дело не в том, что нужно ещё раз approve
- дело не только в cross-account issue

Следующий логичный шаг:

1. Довести truthful UI/runtime state до явного `chrome_policy_blocked`
2. Обновить handoff/docs и собрать свежий bundle
3. Решить, что делать с ordinary Chrome path:
   - считать current Chrome default-profile attach confirmed known issue
   - или сделать новый supported path через отдельный non-default `--user-data-dir`

После каждой итерации пиши:

- что изменено
- как проверено
- что осталось
- % проекта и % текущего блока
