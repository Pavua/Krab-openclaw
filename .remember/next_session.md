# Session 50 — Starter Handoff (Session 49 closed, 2026-05-15 ~19:00)

## TL;DR — Session 49 (длинная диагностика, 14-15.05): 1 commit, 3 patches live, 3 macbook reboots

**main HEAD**: `ad0534e` (Wave 256 + 257: typing-indicator early + bypass guard + codex heartbeat)

Session началась как mlx-benchmark research (gemma-4 throughput на M4 Max), перешла в production troubleshooting после трёх kernel panic reboots, закрылась тремя production patches.

## 🎯 Что сделано (Wave 256–257)

| Wave | Файл | Эффект |
|---|---|---|
| **256** | `src/userbot/llm_flow.py` (после `mark_accepted`) | Typing-indicator активируется сразу после auto-reaction, до segmented_prompt/memory/autoscale/joke. Counter с 1/8h → 2/2 reqs. User видит «Краб печатает...» за ~5 сек после receive (было 15-25 сек). |
| **257-A** | `src/integrations/google_genai_direct.py:58-110` + `src/openclaw_client.py:4176,4266` | `LOCAL_BACKEND_PREFIXES = ("mlx-local-kv4/", "lm-studio-local/", ...)` guard в `is_gemma_model`. Bypass router больше не отправляет local-only aliases в Google GenerateContentRequest и HF lookup. Закрыло 11 events `PYTHON-FASTAPI-7M` (`ClientError: unexpected model name format`) + 2 events `PYTHON-FASTAPI-8S` (`404 Repository Not Found`). |
| **257-B** | `src/userbot/llm_flow.py:1381-1404` | Heartbeat condition: `(received_any_tool_event or _is_codex_cli_route)`. Раньше codex-cli routes никогда не triggered heartbeat (subprocess bypass возвращает один stdout-блок, без tool events). Теперь на 60+ сек stalls placeholder обновляется как «🦀 Codex думает... (Ns)». Tests: 207 passed. |

**Pre-commit ruff**: passed (3 files already formatted, no fixes needed).

## 🐛 Открытые баги (для Session 50)

### 🔥 P0 — Pyrogram chat subscription drop

**Симптом**: после `graceful_restart_triggering_catchup` (network_watchdog) **конкретный chat исчезает** из активных Pyrogram updates. DM работает дальше, но **группа silent**.

**Эмпирика 15.05**:
- 05:38 — последний event для YMB chat (`-1001804661353`)
- 09:09 / 10:01 / 12:21 / 16:01 / 18:13 — `graceful_restart_triggering_catchup` events
- 09:09 — 18:13 — Krab обрабатывал DM `p0lrd` (chat `312322764`) **исправно**
- 05:38 — 18:50 — **0 событий** для YMB, при этом 25+ mentions Krab'а от 4 разных пользователей (включая прямое «Краб, бери его на себя» от owner)
- 18:54 (после fresh restart) — YMB снова видим, ответ за 19 сек

**Гипотеза**: `src/userbot/message_catchup.py` (Wave 46-A) при session recreation **selectively** unsubscribes часть chats. Нужно:
1. Прочитать `src/userbot/message_catchup.py` + `src/core/network_watchdog.py`
2. Найти где Pyrogram session.recreate / iter_dialogs вызывается после catchup
3. Проверить — re-attach handler идёт на **все** dialogs или только на active subset

**Quick test**: Stop+Start Krab возвращает chat. Workaround на сейчас — следить через `silence_auto_owner_typing` events для main групп, если за 30 мин нет — рестартнуть.

### P1 — Heartbeat patch применился, но не verified в prod на длинных stall

Wave 257-B протестирован на 28-сек codex запросе → heartbeat threshold (60 сек) не triggered, как и должно. На реальных 3-min stalls должен показывать «Codex думает... (Ns)». **Нужен один natural slow request чтобы убедиться.**

### P2 — Multi-account codex rotation мёртв

В `~/.codex_accounts/`:
- `primary/auth.json` ✅
- `account2/`, `account3/` — папки есть, но `auth.json` отсутствует

`list_accounts()` фильтрует по `auth.json` → возвращает `logged_in=False` для 2/3. `max_attempts = 2`, но второй `get_next_codex_home()` → `None` → `CodexQuotaExhaustedError` моментально при stall primary.

**Fix**: интерактивно (user в Terminal) — `CODEX_HOME=~/.codex_accounts/account2 codex login` + повторить для account3. После этого rotation оживёт сама (код готов, в `src/integrations/cli_subprocess_bypass.py:341-565`).

### P3 — Routing alias bug в owner panel

`/admin/routing` UI при сохранении truncate'нет полные MLX aliases. Например выбираешь `gemma-4-26B-A4B-it-OptiQ-4bit (14.6 GB)` → сохраняется как `mlx-local-kv4/gemma-4-26b` → 404 на runtime. Это и спровоцировало 11 Sentry events PYTHON-FASTAPI-7M вчера.

Правильный полный alias из `~/.openclaw/agents/main/agent/models.json`:
```
mlx-local-kv4/gemma-4-26B-A4B-it-OptiQ-4bit
```

**Workaround**: через API `POST /api/admin/model/switch` с body `{"model":"mlx-local-kv4/gemma-4-26B-A4B-it-OptiQ-4bit"}`.

**Fix needed**: `src/modules/web_routers/models_admin_router.py` — где UI dropdown собирается, нужен `provider/full_id` join вместо truncation.

## 📊 mlx-benchmark research findings (14-15.05)

Замеры gemma-4-26B-A4B-it на M4 Max 36GB:

| Стек / квант | warm tok/s | Заметки |
|---|---|---|
| 🥇 **vanilla 4bit + MTP spec b=2 t=0** (mlx_vlm direct) | **101** | best result, MTP draft = `guardiangate1775/...-assistant-4bit` |
| vanilla 4bit baseline t=0 (mlx_vlm) | 92.4 | без spec |
| **LM Studio HTTP vanilla 4bit** (старая 0.4.12) | **87.4** | absolute fastest HTTP path |
| vanilla 4bit baseline t=0.3 (mlx_vlm) | 82.6 | |
| LM Studio HTTP OptiQ-4bit | 73.7 | |
| OptiQ mlx_lm direct | 67.9 | |
| vanilla mlx_lm direct | 68.0 | |
| **OptiQ via mlx_lm.server :8088 HTTP** (Krab prod) | **58.7** | HTTP overhead ~14% |
| LM Studio 0.4.13 mxfp4/4bit под compressor pressure | 45-79 | не fair (memory ate perf) |
| **LM Studio 0.4.13 nvfp4 t=0.3** (compressor pressure) | **64.5** | user наблюдал ~60, подтверждено |
| **LM Studio 0.4.13 nvfp4 t=0** (greedy peak) | **76.6** | ≈ mxfp4 (79), noise-level diff 3% |
| **LM Studio 0.4.13 vanilla 4bit t=0.3** (apples-to-apples) | **67.3** | **winner** среди 4bit, +4% vs nvfp4/mxfp4 |
| **LM Studio 0.4.13 vanilla 4bit t=0** (greedy peak) | **79.7** | winner, на 15% быстрее `mlx_lm.server :8088` (58.7) под HTTP |

**Главные выводы**:
- **OptiQ ≈ vanilla 4bit на одинаковом стеке** (TurboQuant не быстрее в throughput, выигрывает только в точности на чувствительных слоях)
- **LM Studio самый быстрый HTTP стек** даже с overhead
- **MTP speculative с правильным 4bit draft даёт ~10%** на vanilla target при t=0 (greedy). На abliterated target — provider mismatch, 0.57-0.81×.
- **mxfp4 на M4 Max нет hw acceleration** — generic FP4 path, медленнее affine 4bit.
- **`gemma-4-26B-A4B-it-assistant` (MTP draft) в LM Studio 0.4.13 UI** не распознаётся как «совместимая черновая модель» — native LM Studio spec для Gemma 4 не реализован

**Артефакты бенчей**: `/Volumes/4TB SSD/bench_tmp/*.py` (mlx_vlm_bench.py, mxfp4_bench.py, lmstudio_bench.py) — persistent через reboot.

**Скачанные таргеты** в `/Volumes/4TB SSD/LMStudio_models/mlx-community/`:
- `gemma-4-26b-a4b-it-4bit` (15.6 GB, vanilla affine)
- `gemma-4-26b-a4b-it-mxfp4` (14.9 GB)
- `gemma-4-26b-a4b-it-nvfp4` (15.6 GB) — **протестирован 15.05 19:35 в LM Studio 0.4.13: 64.5 (t=0.3) / 76.6 (t=0)**

**Скачанный draft**: `/Volumes/4TB SSD/LMStudio_models/guardiangate1775/gemma-4-26B-A4B-it-assistant-4bit` (282 MB, MLX 4bit MTP).

## 🛑 Уроки сессии (записано в memory)

| Memory file | Урок |
|---|---|
| `feedback_ram_pressure_36gb` | Никогда не запускать параллельно :8088 + mlx_vlm bench + LM Studio + HF download. Сумма >28GB committed → kernel panic. |
| `feedback_lmstudio_single_model` | Никогда не держать 2 LM Studio модели loaded одновременно. После `POST /api/v1/models/unload` ВСЕГДА проверять `/api/v0/models` count=0. Body для unload: `{"instance_id":"..."}`, НЕ `{"model":"..."}`. |
| `feedback_krab_ear_separate_project` | Krab Ear — параллельный проект. `new Stop Krab.command` теперь под env flag `KRAB_EAR_STOP_WITH_KRAB=0` (default), не трогает Krab Ear LaunchAgent'ы. |

## ⚡ Quickstart следующей сессии

```bash
# 1. Health check
curl -sS http://127.0.0.1:8080/api/health | python3 -m json.tool
curl -sS http://127.0.0.1:8080/api/admin/routing-active | python3 -m json.tool

# 2. Если Krab лежит:
"/Users/pablito/Antigravity_AGENTS/new start_krab.command"

# 3. Если YMB silent — проверить когда последний event:
grep "1001804661353" ~/.openclaw/krab_runtime_state/krab_main.log | tail -3
# Если > 30 мин назад — Stop+Start Krab

# 4. Sentry проверка:
# через MCP: mcp__krab-yung-nagato__krab_sentry_status statsPeriod=6h

# 5. Bench scripts:
ls /Volumes/4TB\ SSD/bench_tmp/
```

## 🎯 P0 для Session 50 (приоритеты)

1. **Pyrogram chat-drop bug** — root cause investigation в `message_catchup.py` + `network_watchdog.py`. Можно через subagent (general-purpose, read-only, ≤250 слов отчёт).
2. **Verify Wave 257-B heartbeat** — нужен natural slow codex запрос (60+ сек). Можно искусственно прогрузить codex длинным промптом и watch log на `heartbeat` events.
3. **Login account2/account3 codex** — user в Terminal, oneoff.
4. **Routing alias UI fix** — `models_admin_router.py`, low risk patch для panel dropdown.
5. ~~**(Опционально) nvfp4 mlx_vlm bench**~~ — **CLOSED 15.05 19:35**. nvfp4 ≈ mxfp4 на M4 Max (через LM Studio 0.4.13 noise-level diff 3%). FP4 на Apple Silicon идёт через generic path, не натив. Закрывает бенчмарк-цикл.
6. **(Опционально, P5) Production routing → LM Studio HTTP** — vanilla 4bit через LM Studio :1234 даёт **79.7 tok/s** (greedy) / **67.3 tok/s** (t=0.3), что **на 15% быстрее** текущего `mlx_lm.server :8088` (58.7 tok/s HTTP с OptiQ). Apples-to-apples (одни RAM conditions, оба HTTP). Если переключить Krab routing на `lm-studio-local/gemma-4-26b-a4b-it` — +15% throughput бесплатно. Требует: (1) `KRAB_OPENCLAW_BYPASS_ENABLED=1` или похожий gate, (2) `LM_STUDIO_API_KEY` уже в `.env`, (3) test что Gateway не override'нет на cloud.

### P3.5 — LM Studio gemma model routing enablement (новое 15.05 21:30)

Попытка зарегистрировать `gemma-4-26b-a4b-it@4bit` в `models.json` под `providers.lmstudio.models` **не сработала** — Krab API возвращает `model_unknown:lmstudio/gemma-4-26b-a4b-it@4bit` даже после restart с обновлённым catalog.

**Корневая причина**: символ `@` в model.id (LM Studio convention для quant suffix) не валидируется catalog resolver Krab'a. Парсер вероятно режет `gemma-4-26b-a4b-it@4bit` на `gemma-4-26b-a4b-it` + `4bit` или просто rejects по spec char.

**Fix варианты для S50**:
1. **Sanitize layer в `src/core/model_router.py`** — map canonical-safe-id ↔ provider-actual-id. Например catalog id=`gemma-4-26b-a4b-it-lmstudio`, при HTTP-request к LM Studio шлём `model: gemma-4-26b-a4b-it@4bit`.
2. **Rename в LM Studio через `lms load <model> --identifier <safe>`** — переименовать identifier когда загружаем. Тогда `@` не появится в id вообще. Это user-side workaround.
3. **Расширить sanitize в registry validator** на `[a-z0-9_/-]+` + `@` whitelist.

**Текущий state**: `models.json` уже содержит запись `gemma-4-26b-a4b-it@4bit` (добавлено 15.05 21:25). Эту запись нужно либо удалить, либо renames при fix.

Backup до edit: `/Users/pablito/.openclaw/agents/main/agent/models.json.bak_before_lmstudio_add` (но не появился в ls — возможно cp не удался из-за RAM pressure timeout'а).

### 🚀 P5 redesign — MLX HTTP serving alternatives research (15.05 22:00, subagent)

Research subagent выявил **минимум 3 готовых HTTP-сервера** для MLX с speculative decoding — **custom FastAPI не нужен**:

**Tier 1 — HTTP + spec decoding**:
- 🥇 **Rapid-MLX** (`raullenchai/Rapid-MLX`, 2.4k stars, **v0.6.50 от 15.05.2026**) — OpenAI-compat `:8000`, Gemma 4 26B/31B в supported, **DFlash spec уже shipped** (1.3-2× decode), MTP помечен experimental. Drop-in замена для `:8088`. **Action**: `pip install rapid-mlx && rapid-mlx serve mlx-community/gemma-4-26b-a4b-it-4bit --port 8087 --speculative dflash` (или `--mtp`).
- **vllm-mlx** (`waybarrios/vllm-mlx`, 1.2k stars) — vLLM-port для MLX, OpenAI+Anthropic API, `--mtp` flag (verified Qwen3-Next, Gemma 4 vision listed), 400+ tok/s claims, MCP tool calling, batching.
- **Ollama PR #15980** — Gemma 4 MTP speculative pending merge. Watch для официальной поддержки.

**Tier 2 — HTTP без MTP**:
- MLX-Omni-Server (`madroidmaq`) — OpenAI+Anthropic на `:10240`
- mlx-openai-server (`cubist38`) — FastAPI wrapper, no spec
- oMLX (`jundot`) — menu-bar managed с SSD caching

**Tier 3 — не подходит**:
- MTPLX — Qwen-only
- vLLM upstream — Linux/CUDA
- TabbyML — нет MLX backend

**Test plan для S50**:
1. Install **Rapid-MLX** в worktree-venv (не глобально, чтобы не ломать system Python)
2. Load `gemma-4-26b-a4b-it-4bit` через rapid-mlx serve `:8087` (или другой свободный port)
3. Прогнать `/Volumes/4TB SSD/bench_tmp/lmstudio_bench.py` указав new endpoint
4. Если DFlash spec работает: **сравнить с 101 tok/s** (наш утренний рекорд mlx_vlm + MTP). Если ≥80% → переключить Krab routing на Rapid-MLX HTTP. **Это закрывает P5 без custom FastAPI**.

**Sources**: GitHub Rapid-MLX, vllm-mlx, MTPLX, mlx-omni-server, [Google blog Multi-Token Prediction for Gemma 4](https://blog.google/innovation-and-ai/technology/developers-tools/multi-token-prediction-gemma-4/), [mlx-vlm issue #981](https://github.com/Blaizzy/mlx-vlm/issues/981), [Ollama PR #15980](https://github.com/ollama/ollama/pull/15980).

## 📂 Текущее состояние (2026-05-15 ~19:00)

- **Krab**: started ~18:54 после моего restorative restart, health=ok
- **Routing**: picked=actually=`codex-cli/gpt-5.5`, status=ok
- **YMB chat** (`-1001804661353`): обратно в подписке, ответил за 19 сек на test ping 18:54
- **DM** (`p0lrd`, `312322764`): работал стабильно весь день
- **Sentry 24h**: чисто (только Krab Ear hanging — параллельный проект)
- **Memory**: 35GB used / 329MB free (compressor 7GB) — baseline для текущей рабочей конфигурации
- **LM Studio**: 0.4.13 active, 0 моделей loaded
- **`:8088` mlx_lm.server**: OptiQ-4bit loaded, не routed сейчас (Gateway → cloud через paid_gemini_guard)
- **15940/16063 tests collected** (2 collection errors в test_memory_doctor_all_db.py — не блокер)
