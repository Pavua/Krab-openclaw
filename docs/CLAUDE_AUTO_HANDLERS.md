# Auto-generated handlers (172 handle_* функций)

Phase 2 Waves 1-18 + Session 35-38. Модули в `src/handlers/commands/` (24 файла):
text_utils / chat / scheduler / voice / memory / social / ai / swarm / translator / system / admin / cli / fileio / group_admin / content / state / observability / memory_admin / policy / _shared / engine_commands / curator_commands + `src/handlers/command_handlers.py`

Обновлено: Session 38 (05.05.2026). Актуальный счётчик:
```bash
grep -hE "^async def handle_" src/handlers/commands/*.py src/handlers/command_handlers.py | sort -u | wc -l
```

`!access`, `!acl`, `!agent`, `!alias`, `!archive`, `!ask`
`!autodel`, `!backup`, `!bench`, `!block`, `!blocklist`, `!browser`
`!budget`, `!cap`, `!catchup`, `!chatban`, `!chatpolicy`, `!claude_cli`
`!clear`, `!clear_session`, `!codex`, `!collect`, `!config`, `!context`
`!costs`, `!cronstatus`, `!curator`, `!debug`, `!del`, `!diag`
`!diagnose`, `!digest`, `!e`, `!emoji`, `!engine`, `!eval`
`!explain`, `!export`, `!fix`, `!forget`, `!fwd`, `!gemini`
`!grep`, `!health`, `!help`, `!hs`, `!id`, `!inbox`
`!ls`, `!mac`, `!memo`, `!memory`, `!model`, `!models`
`!monitor`, `!news`, `!note`, `!notify`, `!opencode`, `!panel`
`!pin`, `!poll`, `!proactivity`, `!purge`, `!qr`, `!quiz`
`!quota`, `!rate`, `!react`, `!read`, `!reasoning`, `!recall`
`!remember`, `!remind`, `!reminders`, `!report`, `!restart`, `!rewrite`
`!rm_remind`, `!role`, `!say`, `!schedule`, `!scope`, `!screenshot`
`!search`, `!set`, `!shop`, `!stats`, `!status`, `!stopwatch`
`!summary`, `!swarm`, `!sysinfo`, `!timer`, `!todo`, `!translate`
`!translator`, `!trust`, `!unarchive`, `!unblock`, `!unpin`, `!uptime`
`!version`, `!voice`, `!watch`, `!web`, `!who`, `!whois`, `!write`

Beta (Session 17): `!mem` — быстрый поиск по архиву памяти; `!chado` — chado-sync агент; `!filter` — фильтрация сообщений по паттерну
