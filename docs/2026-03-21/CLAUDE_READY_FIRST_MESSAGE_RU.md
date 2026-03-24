# Claude Ready First Message — 21.03.2026

Продолжаем проект `Краб` из репозитория `/Users/pablito/Antigravity_AGENTS/Краб`.

Используй приложенные файлы как source of truth.
Не опирайся на старые пересказы и не восстанавливай картину по памяти.

Сначала прочитай в таком порядке:

1. `START_HERE_RU.md`
2. `HANDOFF_PORTABLE_RU.md`
3. `KNOWN_AND_DISCUSSED_RU.md`
4. `MASTER_PLAN_SOURCE_OF_TRUTH.md`
5. `MASTER_PLAN_VNEXT_RU.md`
6. `LM_STUDIO_MCP_SETUP_RU.md`

Текущая truthful картина:

- web panel `:8080` поднята
- OpenClaw gateway `:18789` поднят
- Voice Gateway `:8090` поднят
- Krab Ear поднят
- warmup truthful: `google-gemini-cli/gemini-3.1-pro-preview`, `active_tier=paid`
- USER2 Codex MCP usable
- browser truth и owner/debug Chrome path уже разведены
- live progress проекта около `91%`
- baseline master-plan около `31%` и не равен live progress

Главный current blocker:

- ordinary Chrome attach к default profile на Chrome `146.0.7680.154`
  блокируется политикой самого Chrome
- helper-log содержит:
  `DevTools remote debugging requires a non-default data directory`
- это не просто permission prompt и не сводится к “ещё раз approve”

Следующий правильный фокус:

1. Не потерять truthful состояние и уже обсуждавшиеся развилки
2. Довести truthful UI/runtime state по ordinary Chrome blocker
3. Обновить handoff/docs и при необходимости собрать свежий bundle
4. Решить стратегию:
   - либо принять default-profile attach как confirmed known issue
   - либо построить новый supported path через non-default `--user-data-dir`

После каждой итерации пиши:

- что изменено
- как проверено
- что осталось
- % проекта и % текущего блока
