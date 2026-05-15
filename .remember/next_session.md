# Session 53 — Starter Handoff (Session 52 closed, 2026-05-16)

## TL;DR — Session 52 (~6 часов): 5 commits + comprehensive multi-stack bench + LOCAL VISION LIVE END-TO-END

**main HEAD**: `d4ff0e6` (feat(media): route frame describe to local LM Studio Gemma 4 (S52 P0))

Session 52 closed the **cloud Gemini vision describe regression** (S51 discovered:
3/3 frame describes timeout @ 25s each). Implementation: local Gemma 4 26B
vanilla via LM Studio :1234 для `_describe_video_frame`. **Verified end-to-end
in production**: Krab корректно описал содержимое video_note (3 frames in ~6
секунд) после reply от owner.

## 🎯 Что сделано (production)

| Commit | Fix | Tests |
|---|---|---|
| `1e5fb00` | P0 diag — `_resolve_recent_active_chats` diagnostic logging | (S51) |
| `497f7bf` | P3 — drop hardcoded mlx-local-kv4 → dynamic discovery | (S51) |
| `ad0d20b` | reply→video_note media extraction (S51) — fixes silent ignore | (S51) |
| `2b820dc` | S51 handoff docs | (S51) |
| **`d4ff0e6`** | **S52 P0** — `KRAB_LOCAL_VISION_ENABLED=1` → local LM Studio Gemma 4 vision describe. Closes cloud timeout regression. | **+9 tests** |

## 🏁 Comprehensive bench results (Session 52 ~3 hours, 7+ models × 4 stacks)

### Winner матрица

| Combo | Text tok/s | Vision (s) | Quality | Verdict |
|---|---|---|---|---|
| 🏆 **LM Studio + Gemma 4 26B vanilla** | **68.5** | **1.7-2.2** | ✅ clean | **PRODUCTION** |
| Rapid-MLX + Gemma 4 vanilla | 66.7 | 8-11 (verbose 2000 tok) | ✅ but ignores max_tokens | Alt |
| mlx_lm.server + Qwen 3.5 35B-A3B MoE | 68.9 | n/a (text only) | ⚠️ thinking-process leak | Defer |
| LM Studio + Qwen 3.5/3.6/GLM-4.6V/Claude-distilled | 8-48 | broken | ❌ thinking template quirks (LM Studio update May 16 may worsened) | Skip |
| LM Studio + Gemma 4 OptiQ | n/a | n/a | ❌ GPU stream error | Broken backend |
| MTPLX/DFlash | n/a | n/a | misnamed weights (no real MTP head) | Defer indefinitely |

### Critical learning: stack matters as much as model

- **Qwen 3.5 35B-A3B**: LM Studio 7.9 tok/s vs mlx_lm.server 68.9 tok/s = **8.7× difference** purely from serving stack.
- LM Studio's mlx-engine has thinking-mode template quirks с Qwen / GLM / Claude-distilled — returns empty `content` field, всё в `reasoning`.
- Gemma 4 vanilla — **единственная модель которая работает clean в LM Studio multimodal** (no template quirks, fastest, smallest verbose output).

### Models tested but rejected

- **Qwen3.5-9B-VLM** (5.7 GB): 24.8 tok/s text, 28s cold vision describe — slower than 26B Gemma despite smaller
- **Qwen 3.6 27B**: LM Studio returns empty content (template parser broken на Qwen 3.6 после May 16 update)
- **Gemma 4 Claude-distilled**: `<|channel>thought` token leak (Issue #899) в content output
- **GLM-4.6V-Flash (9B)**: 48.3 tok/s text but vision variable (3.75-17.6s)
- **MTPLX-Optimized-Speed (Youssofal)**: `mtplx inspect` says "Model has no MTP head" — misnamed, не actually MTP-equipped
- **DFlash variants (z-lab, mlx-community)**: draft models exist (821 MB DFlashDraftModel arch) но Rapid-MLX `--enable-dflash` automation broken для Gemma 4

## 🏗️ Architecture после S52

```
Krab text routing → codex-cli/gpt-5.5 (cloud, primary)
                    ↘ openclaw/cloud Gemini (fallback)
                    
Krab vision describes (NEW) → LM Studio :1234 + Gemma 4 26B vanilla
                              ↘ cloud Gemini (fallback if local empty)

LM Studio config: 
  - Gemma 4 26B-A4B-it@4bit loaded as `krab-vision-primary` (14.57 GiB)
  - TTL=infinite (always loaded для tier-1 latency)

RotorQuant :8088 mlx_lm.server (OptiQ-4bit) — untouched, their research
```

## 📊 Production E2E verification (логи 2026-05-16 01:29)

```
01:29:36 processing_ai_request msg_id=768744 (reply to 768657 video_note)
01:29:39 perceptor_video_frames_extracted frames=3
01:29:42 frame_describe_local_success idx=0 char_count=194   ← 3s for vision
01:29:43 frame_describe_local_success idx=1 char_count=132   ← 1s additional
01:29:44 frame_describe_local_success idx=2 char_count=174   ← 1s additional
... LLM context augmented with frame descriptions ...
01:33:33 cli_subprocess_complete_done   ← Krab final response sent
```

Krab response в чат:
> "В конце кадр уходит в темноту: виден силуэт человека слева и какие-то
> очертания окон/дверей внизу. Технически: кружок обработался как
> `video_note`, `message_id=768657`, 3 кадра успешно разобраны."

## 🐛 Открытые items для Session 53

### 🟡 P1 — Routing race condition после tests

S52 P3 unit test `test_switch_mlx_local_kv4_prefix_session51_p3` использует
`mlx-local-kv4/custom-experimental-model-xyz` через POST /api/admin/model/switch.
В production WIP это committed в `active_model.json` если test runs against
running Krab (которое не должно случаться — это unit test). НО я наблюдал
запись `actually_used: mlx-local-kv4/custom-experimental-model-xyz` после
restart Krab — может из artifact `~/.openclaw/agents/main/agent/active_model.json`.

**Fix**: либо test использует separate temp config, либо `/api/admin/model/switch`
sanitize'ить против "experimental" patterns (low risk).

Workaround applied: manually reverted к codex-cli/gpt-5.5 через POST switch.
**Verify в S53**: после reboot routing остаётся codex-cli, нет drift к
experimental-model-xyz.

### 🟡 P1 — Verify health.openclaw probe stability

После S52 restart: `openclaw: false` в health endpoint первые ~10 сек, потом
stabilized к `true` (но logs don't show stabilization event clearly). Не
блокер, но monitor.

### 🟢 P2 — Translator local migration (Phase 2 carryover)

`src/core/translator_engine.py:117` всё ещё `force_cloud=True` для auto-translate.
Highest frequency cloud-burner. Pattern для миграции уже proved working
(S52 vision describe). Apply same routing logic для translator через
KRAB_LOCAL_TRANSLATOR_ENABLED.

### 🟢 P2 — Photo describe / OCR migration

`src/handlers/commands/content_commands.py:288` (`cmd_img`) и `:388` (`cmd_ocr`)
имеют hardcoded `force_cloud=True`. Reuse pattern S52 для local route.

### 🟢 P3 — Local draft verifier (Phase 3 carryover)

New module `src/core/local_draft_verifier.py` для 20% sample cross-AI verify
(subagent S52 design). ~120 LOC + hook в llm_flow + JSONL log + Prometheus
drift metric.

### 🟢 P4 — Clean up routing test artifact

`tests/unit/test_models_admin_router_wave144.py` use `mlx-local-kv4/custom-experimental-model-xyz`
might persist if test fixtures leak. Verify fixture isolation OR change
test model id to clearly-fake `_TEST_ONLY_` prefix.

### 🟢 P5 — Disk cleanup (~894 GB models, найти "мусорные")

User has 89+ models на 4TB SSD (~994 GB!). Candidates для cleanup:
- **Qwen 3.6 27B variants** (broken в LM Studio): qwen/qwen3.6-27b, qwen3.6-27b-ud-mlx, Youssofal/Qwen3.6-35B-A3B-Abliterated-Heretic
- **Qwen 3.5 9B VLM** (slower than Gemma 4 26B): qwen3.5-9b-mlx-vlm
- **MTPLX-Optimized-Speed** (misnamed, no real MTP)
- **Gemma 4 Claude-distilled** (`<|channel>thought` leak)
- **Gemma 4 OptiQ** (GPU stream error в LM Studio, RotorQuant использует — НЕ удалять)

Это потенциально 100+ GB освободить. **Не делаю автоматически — review с тобой first**.

## ⚡ Quickstart следующей сессии

```bash
# 1. Health
curl -sS http://127.0.0.1:8080/api/health | python3 -m json.tool
curl -sS http://127.0.0.1:8080/api/admin/routing-active | python3 -m json.tool

# 2. Verify local vision still works:
LOG=~/.openclaw/krab_runtime_state/krab_main.log
grep "frame_describe_local_success" $LOG | tail -3   # последние успехи
grep "lmstudio_frame_describe_failed" $LOG | tail -3 # если есть HTTP errors

# 3. LM Studio status (Gemma должна быть loaded):
~/.lmstudio/bin/lms ps   # ищи krab-vision-primary (gemma-4-26b-a4b-it@4bit)
# Если unloaded — auto-load on first request (LM Studio JIT)

# 4. If Krab routing drifted к experimental:
curl -X POST http://127.0.0.1:8080/api/admin/model/switch -H 'Content-Type: application/json' \
  -d '{"model":"codex-cli/gpt-5.5"}'

# 5. Bench data:
ls /Volumes/4TB\ SSD/bench_tmp/result_*.json   # все S52 bench results
```

## 📊 Текущее состояние (2026-05-16 ~01:34)

- **Krab**: live, routing **codex-cli/gpt-5.5** (после revert)
- **LM Studio :1234**: Gemma 4 26B vanilla loaded as `krab-vision-primary` (14.57 GiB)
- **RotorQuant :8088 mlx_lm.server**: launchd-managed, OptiQ-4bit configured
- **OpenClaw Gateway :18789**: running
- **KrabEar, Voice Gateway**: green
- **Local vision**: VERIFIED working (frame_describe_local_success × 3 в last test)
- **RAM**: tight (~3-7 GB free) — 15 GB Gemma loaded + 15 GB RotorQuant OptiQ + Krab + Krab Ear + macOS

## 🛑 Уроки сессии

| Lesson | Why matters |
|---|---|
| Stack ≠ model: Qwen 3.5 35B даёт 7.9 tok/s в LM Studio vs 68.9 в mlx_lm.server | Test через **multiple** backends перед dismiss'ing model |
| LM Studio update breaks thinking-mode templates | После updates LM Studio re-bench critical models |
| MTPLX/DFlash misnamed weights | Always `mtplx inspect` model перед assuming spec decode support |
| Unit test POST switch persists в production active_model.json | Test fixtures need isolation OR clearly-fake model ids |
| Cloud Gemini vision describe regressed in May 2026 | Local vision = production-grade alternative ready |

## 🎯 P0 для Session 53 (priorities)

1. **Monitor production**: проверить через 24h что local vision стабильно работает (no spike в `lmstudio_frame_describe_failed`)
2. **Phase 2: Translator migration** к local — highest-freq cost saver, тот же pattern что S52 vision
3. **Phase 3: Local draft verifier** — design ready, 120 LOC implementation
4. **Cleanup tests + disk** — fix routing race condition, delete confirmed-bad models (100+ GB savings)
5. **(Optional) Newer Qwen 3.5/3.6 models** через **mlx_lm.server** stack — может work clean (не через LM Studio)

Удачной сессии 🦀

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
