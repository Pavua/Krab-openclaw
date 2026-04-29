# Session 30 — Starter Handoff (after Session 28+29 close + part 30 panel/models, 2026-04-30)

## TL;DR (что получишь в следующей сессии)

- **Branch `fix/daily-review-20260421`**: 645+ commits ahead of `origin/main` (1 trivial README conflict)
- **Phase 2 splits complete**: command_handlers.py 19637 → 1226 LOC (−93.8%), 21 waves, 22 modules в `src/handlers/commands/`
- **13 learning features (A-M)** + **27 idea modules** landed (1, 2, 3, 4, 5, 6, 7, 8, 10-26, 28-38)
- **VPN integration LIVE** обоими сторонами (Phase A MCP tools + B brain endpoint + C alerts bridge)
- **Models catalog** расширен до **151 моделей в 13 провайдерах** (gpt-5.5, gpt-5.6, o3/o4-mini, Claude 4.6/4.7/4.8, Gemini 3.x, DeepSeek V4, Qwen 3.5/3.6 LM Studio)
- **Panel routing**: `/` → V4 (Liquid Glass), `/legacy` → старый landing
- **Primary model вернули на `codex-cli/gpt-5.5`** + fallbacks убрали openai-codex (был red-state)

## КРИТИЧЕСКИЕ БАГИ найдены в feedback из чатов (нужно fix)

### Bug 13 — Krab не видит images в reply context
User: «изображения он видит только если ему их именно отправить в телеграме, если к примеру ответить на какое-то сообщение — не видит»
- Текущий `reply_preprocessor` (Session 28 commit `74a7b95`) extracts text из reply_to.text/caption но **НЕ pulls media** из reply_to_message
- Fix: в `src/userbot/reply_preprocessor.py` extract `reply.photo/video/document/animation` → augment vision pipeline

### Bug 14 — Krab "печатает..." бесконечно на отдельных вопросах
User: «бывает долго что-то "печатает" и это почти бесконечно, хотя на другие вопросы отвечает в том же чате»
- Hypothesis: LLM streaming hangs или tool execution loop infinite
- Возможные причины:
  - `process_video_message` (commit `c0dba1a`) с broken ffmpeg path → 75s timeout per frame × 3 = до 4 min stall
  - Self-correction loop (Idea H) re-genering forever если LLM не возвращает `ok:true`
  - Smart routing LLM classifier 2s timeout зависает (LM Studio 401 issue)
- Fix: extra timeouts на каждом этапе + global hard cap 60s per response

### Bug 15 — Krab предлагает password/admin доступ вместо VPN MCP tools
User: «с впн сервером он не очень хотел помогать, мы же вроде сделали ему поддержку этого, а он предлагает мне дать ему пароли и доступ через админку впн»
- VPN MCP tools зарегистрированы (commit `cc19f7b` + refactor `0008607`) но **system prompt** Krab не упоминает что у него есть VPN tools
- LLM не знает что может вызывать `vpn_list_clients`, `vpn_get_config(client_name)` etc
- Fix: добавить в `src/userbot/access_control.py:_append_runtime_constraints` блок:
  ```
  Доступные tools для VPN операций (call через function-call):
  - vpn_list_clients() — список клиентов x-ui панели
  - vpn_get_config(client_name) — vless link для клиента
  - vpn_panel_health() — статус панели
  - vpn_traffic_stats(client_name) — расход трафика
  Используй эти tools вместо запроса паролей.
  ```

### Bug 16 — Krab banned в чате How2AI
User в чате (msg 78934): «аа, он же здесь забанен, ебт»
- Krab (yung_nagato) забанен в групповом чате How2AI (-1001587432709)
- chat_ban_cache работает (commit Session 28), но user может захотеть unban
- Action: `!chatban list` чтобы увидеть, `!chatban remove -1001587432709` если admin вернул permissions

### Issue 17 — Old panel showed at / (FIXED в этой сессии commit `ab05d70`)
- `/` теперь serves V4 dashboard
- `/legacy` сервит старый landing
- `/legacy/inbox`, `/legacy/costs` и т.д. (existing paths) сохранены

### Issue 18 — Models selector incomplete (FIXED в этой сессии)
- models.json: 151 моделей в 13 providers (было ~10 в total)
- codex-cli, openai-codex, codex теперь имеют GPT-5/5.4/5.5/5.5-pro/5.6 + o3/o3-mini/o3-pro/o4-mini
- openai (API): gpt-3.5/4/4o/4.1/5/5-mini/5.5/5.5-pro/5.6 + o1/o1-pro/o3/o3-pro/o4-mini
- Anthropic: claude-3 series + 3.5/3.7/4/4.5/4.6/4.7/4.8 (haiku/sonnet/opus)
- Google: full Gemini 1.5/2.0/2.5/3/3.1 (flash/pro/preview)
- DeepSeek: chat/coder/reasoner/V3/V4
- Qwen Portal: 13 моделей (turbo/plus/max/2.5/3/3.5/3.6, qwq, reasoner)
- LM Studio: 13 локальных MLX моделей

### Issue 19 — primary вернули на codex-cli/gpt-5.5 (FIXED)
- В предыдущей сессии user manually переключил на 5.4 потому что в catalog не было 5.5
- После expansion catalog → primary вернул `codex-cli/gpt-5.5`, fallbacks: openai/gpt-5.5 → claude-opus-4-7 → gemini chain
- Removed openai-codex из fallbacks (был red в panel)

## VPN integration architecture (final)

| Component | Where | What |
|---|---|---|
| `vpn_list_clients`, `vpn_get_config` | Krab MCP via subprocess | Calls `/Users/pablito/Antigravity_AGENTS/VPN/list_clients.command` and `get_client_config.command --json` |
| `vpn_panel_health` | Krab `vpn_tools.py` | HTTP probe |
| `vpn_traffic_stats` | Krab read-only sqlite | `client_traffics` table |
| `POST /api/inbox/create-vpn-alert` | Krab inbox bridge | VPN watchdogs (cert_guard, disk_guard, watchdog_vpn_panel, bruteforce_audit, endpoint_failover_check) post via `krab_alert.command` shell wrapper |
| `POST /api/vpn/help` | Krab brain endpoint | VPN bot `@pablito_vpn_bot` proxies friend questions → Krab LLM with persona drift |

**Single source of truth** для `build_vless_link()` — `vpn_bot.py` в VPN repo. Krab MCP tools тонкие subprocess wrappers.

## KRAB_WEB_KEY (sync'нут оба .env)

- `/Users/pablito/Antigravity_AGENTS/Краб/.env` ← `KRAB_WEB_KEY=aQio6Iwr...`
- `/Users/pablito/Antigravity_AGENTS/VPN/alerts.env` ← same key
- Verified: keys match (`aQio6Iwr...`)

## Session 30 work (commits ab05d70 + earlier)

- `ab05d70` — `/` → V4 dashboard, `/legacy` → old landing, primary вернули gpt-5.5
- models.json расширен (151 моделей)
- VPN integration smoke tests PASSED:
  - `list_clients.command` returns 24 clients JSON ✓
  - `/api/inbox/create-vpn-alert` creates inbox items ✓
- Bug 13/14/15 documented (нужно fix в next session)

## Backlog для Session 31 (приоритет)

### P0 — Bug fixes
1. **Bug 13**: extract media из `reply_to_message` в `src/userbot/reply_preprocessor.py`. Test cases: photo в reply, video в reply, document в reply
2. **Bug 14**: investigate "infinite typing" — добавить hard 60s cap на response generation. Probable culprits: `process_video_message` ffmpeg, self-correction re-gen loop, smart routing LLM 2s timeout
3. **Bug 15**: добавить VPN tools awareness в system prompt (`access_control.py:_append_runtime_constraints`). Test: спросить Krab "дай конфиг для Anya" → должен вызвать `vpn_get_config("Anya")` через function-call
4. **Bug 16**: unban Krab в How2AI чате (manual action: попросить admin chat unban yung_nagato или `!chatban clear`)

### P1 — Activations & test
5. Включить env flags по очереди: `KRAB_TODO_EXTRACTION_ENABLED=1` (passive log), `KRAB_JOKE_CALIBRATION_ENABLED=1`, `KRAB_MULTI_PERSONA_ENABLED=1`, `KRAB_AB_TESTING_ENABLED=1`
6. Test VPN integration end-to-end: ты в Krab DM → "дай конфиг для Anya" → vless link
7. Smoke tested: `/api/inbox/create-vpn-alert` creates items ✅ (verified)

### P2 — Architecture
8. Final merge to main (`git merge --no-ff fix/daily-review-20260421`, 1 trivial README conflict)
9. WAL flush wait sentinel перед rapid respawn (PYTHON-FASTAPI-5W transient — happens на rapid Stop+Start cycles)
10. **Investigate openai-codex provider** — он never working, why в fallback chain previously?

### P3 — Optional ideas not landed
- Idea 9 (parallel tool execution) — heavy openclaw_client work
- Idea 27 (archive.db SQLCipher encryption)

## Operational quick reference

```bash
cd /Users/pablito/Antigravity_AGENTS/Краб
cat .remember/next_session.md  # this file

# Krab control
"/Users/pablito/Antigravity_AGENTS/new Stop Krab.command"
"/Users/pablito/Antigravity_AGENTS/new start_krab.command"

# After Stop иногда нужен kickstart (KeepAlive Crashed-only):
launchctl kickstart -k gui/$(id -u)/ai.krab.core

# Health
curl -sS http://127.0.0.1:8080/api/health/lite | python3 -m json.tool

# Models catalog (verify expansion landed):
curl -sS http://127.0.0.1:8080/api/model/catalog | python3 -c "
import json,sys
d=json.loads(sys.stdin.read())
print('cloud_presets:', len(d.get('catalog',{}).get('cloud_presets',[])))
"

# DB doctor (если disk I/O error на boot)
venv/bin/python scripts/memory_doctor.py --all-db --json

# Inbox cleanup
venv/bin/python scripts/inbox_bulk_ack.py --age-hours 24 --kind proactive_action --severity warning --target done

# VPN helpers (read-only)
/Users/pablito/Antigravity_AGENTS/VPN/list_clients.command
/Users/pablito/Antigravity_AGENTS/VPN/get_client_config.command <email> --json

# Tests
venv/bin/python -m pytest tests/unit/ -q --tb=line --timeout=30
```

## Operational notes (важно)

- Pre-commit hook иногда auto-stage'ит файлы соседних агентов в commit — verify после dispatch
- Memory pressure: при rapid Stop+Start ловим disk I/O error (PYTHON-FASTAPI-5W). Mitigation: wait 30s между cycles
- Pyrogram session corruption: kraab.session может corrupt'нуться (Apr 26 + Apr 29). Recovery via `sqlite3 .recover` (preserves peers)
- Multi-agent dispatch: Sonnet работает плотно на parallel (5-7 agents OK), Haiku падает на context size (CLAUDE.md тяжёлый)
- Reasoning depth: medium fine для оркестрации, high когда архитектурные решения

## Key files modified в Sessions 28-30

### Krab repo (645+ commits)
- `src/handlers/commands/*.py` — 22 modules (Phase 2 split)
- `src/core/vpn_tools.py`, `vpn_brain.py` — VPN integration
- `src/modules/web_routers/vpn_brain_router.py`, `inbox_router.py` — endpoints
- `src/userbot/access_control.py` — multi-persona + AB + mood + goals + persona drift suffixes
- `src/userbot/reply_preprocessor.py` — Bug 9+3+10 fix (но нужен Bug 13)
- `src/userbot_bridge.py` — VPN media handler + bridge tick + bootstrap singletons
- `src/bootstrap/pyrogram_patch.py` — proper accessor wrap (NoneType guard)
- `src/modules/web_routers/pages_router.py` — `/` → V4 routing
- `~/.openclaw/agents/main/agent/models.json` — 151 моделей в 13 провайдерах
- `~/.openclaw/openclaw.json` — primary `codex-cli/gpt-5.5`
- `~/.openclaw/krab_runtime_state/*.json` — singleton state files

### VPN repo (PR #2 Pavua/vpn-3x-ui-ops)
- `vpn_bot.py` — pure stdlib, ask_krab_brain proxy для Krab
- `krab_alert.command` — shell wrapper для watchdogs
- `get_client_config.command` — JSON helper для Krab MCP tools
- `list_clients.command` — JSON list 24 clients
- 5 watchdogs (cert_guard/disk_guard/watchdog_vpn_panel/bruteforce_audit/endpoint_failover_check) wired

## Memory state (key snapshot)

- archive.db: 753k+ messages, 72k chunks
- response_feedback: 50 records (Feature A — successful response retrieval boost)
- chunk_clusters/cluster_meta: empty (нужен first recluster — `scripts/memory_recluster.py`)
- message_media_summaries: 1 (Feature E — multi-modal memory, нужны wire-up в bridge after process_video)
- `aQio6Iwr...` — KRAB_WEB_KEY synced
- inbox: open=22, stale=4

## Active learning singletons (state в `~/.openclaw/krab_runtime_state/`)

chat_ban_cache, chat_response_policy, owner_presence, repl_session_audit, proactive_suggestions, owner_mood, chat_persona_profile, swarm_channels, named_entities, anomaly_baselines, sensitive_chats, auto_translate_chats, scheduled_replies, tool_composition, joke_calibration, voice_fingerprints, ab_experiments, session_goals.
