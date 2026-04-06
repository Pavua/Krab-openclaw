# Handoff для новой сессии

## State (2026-04-06, session batch 7-8)
Коммиты на `main` за эту сессию:
- `6306885` — fix(swarm): warm-up peer cache for team clients via get_dialogs
- `876f3a5` — feat(swarm): per-team Telegram accounts for swarm broadcasts (357 lines)
- `00d6a41` — refactor(pyrofork): unify venv, remove dual-Pyrogram workarounds

## Что сделано этой сессией

### 1. Pyrofork унификация — ✅ DEPLOYED
- Убрали 4 `getattr`-костыля (send_reaction, CreateForumTopic×2, inspect.signature)
- Единый `venv/` (pyrofork 2.3.69, Python 3.13) для runtime + MCP + тестов
- MCP LaunchAgents переустановлены (`.venv/` → `venv/`)
- CLAUDE.md обновлён
- 31/31 тестов swarm_channels проходят

### 2. Per-team Telegram аккаунты для свёрма — ✅ DEPLOYED, но E2E failed
- SwarmChannels: `_team_clients` dict + `_resolve_client()` + fallback на main
- Userbot lifecycle: background start/stop team clients
- Config: `SWARM_TEAM_ACCOUNTS_PATH` → JSON loader
- Новый скрипт: `scripts/auth_swarm_account.py`
- +10 новых тестов (41/41 pass)
- 4 аккаунта авторизованы и добавлены в Forum группу через yung_nagato
- **Текущая проблема**: peer cache на team clients пуст → CHAT_ID_INVALID при send_message
- **Фикс уже в коде** (коммит `6306885`): `get_dialogs(limit=50)` warm-up при старте
- **Нужен рестарт Краба** чтобы warm-up сработал

## Team accounts mapping
| Команда | Username | User ID | Телефон | Session file |
|---------|----------|---------|---------|--------------|
| traders | @p0lrdp_AI | 1861168302 | +40724455794 | swarm_traders.session |
| coders | @p0lrdp_worldwide | 5929474128 | +66959272975 | swarm_coders.session |
| analysts | @hard2boof | 6539946601 | +6282280748457 | swarm_analysts.session |
| creative | @opiodimeo | 5920778135 | +639355619567 | swarm_creative.session |

Конфиг: `~/.openclaw/krab_runtime_state/swarm_team_accounts.json`
Сессии: `data/sessions/swarm_*.session`
Forum группа: 🐝 Krab Swarm (chat_id: -1003703978531)

## ACL архитектурная особенность
OWNER в Krab = **yung_nagato** (userbot, session: `kraab.session`).
Оператор **p0lrd** (id: 312322764) имеет **FULL** доступ (не OWNER).
Команды для p0lrd: в чат с @yung_nagato (id: 6435872621), не в Saved Messages.

## Следующие шаги (приоритет)
1. **Рестарт Краба** → проверить warm-up team clients → E2E тест `!swarm traders`
   - Должны увидеть в логах: `swarm_team_client_warmed_up` для всех 4 команд
   - Сообщения в Forum должны приходить от @p0lrdp_AI / @p0lrdp_worldwide / @hard2boof / @opiodimeo
2. **Signal gateway**: SSE `fetch failed`, LaunchAgent `ai.openclaw.signal-cli` exit code 1 — диагностировать
3. **Mercadona навигация**: нужны логи терминала при воспроизведении
4. **Удалить `.venv/`** после проверки что всё работает на едином `venv/`

## Проверки после рестарта
```bash
# 1. Грепнуть warm-up логи
grep swarm_team_client_warmed_up ~/.openclaw/krab_runtime_state/krab_main.log

# 2. Послать тест через MCP
# (через telegram_send_message на @yung_nagato)
!swarm traders тест: BTC тренд, одно предложение

# 3. Проверить что сообщения в Forum
# приходят от @p0lrdp_AI, а не от @yung_nagato
```

## Известные особенности runtime
- OpenClaw gateway после рестарта Краба нужно стартовать вручную:
  `PATH=/opt/homebrew/bin:$PATH openclaw gateway start`
- Crash при stop (`CancelledError` в `session.ping_task`) — pre-existing pyrofork issue, не блокер
- Канонические лаунчеры: `new start_krab.command` / `new Stop Krab.command`
- Krab logs runtime: `~/.openclaw/krab_runtime_state/krab_main.log`
- Единый venv: `venv/` (pyrofork 2.3.69, Python 3.13)
