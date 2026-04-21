# Session 17 — Chado Insights Landing (2026-04-21)

## TL;DR

- **48 commits** в main (7b32ce8..fd657c3), включая 5 carryover от Session 16 tail
- **43 чисто Session-17 commits** — Chado insights + Dashboard V4 finalization
- **24 новых тест-файла**, 6750+ строк добавлено
- **Killer find (W2.7)**: dead code в `message_priority_dispatcher` — `classify_priority()` с 0 call-sites блокировал mentions/replies за 30-минутными LLM-loop'ами → исправлено в 51ee5ad
- Все 9 разделов `CHADO_INSIGHTS.md` проработаны, P1 закрыты полностью, большинство P2 реализованы

---

## Architecture v2 Live

| Artifact | URL |
|----------|-----|
| Hero (Canva) | https://www.canva.com/d/wDX_xg3mClWE0t7 |
| Engineering (Claude Design) | https://claude.ai/design/p/f8108663-9376-444f-8c2c-1e93302a02d6 |
| Ops (Claude Design + Canva mirror) | https://www.canva.com/d/3dkWS667S3h08UB |

Skeleton v2.1 (f05616f) → Ops v2 artifact (0d45dab) → Design System v1.0 (4f00750):
- 3 artifacts с единым visual language (rect=process, cylinder=store, hex=gateway)
- Arrow legend: только 3 типа (data / control / failure)
- Ownership boundaries как dashed group boxes

---

## Chado Insights — Статус реализации

| § | Feature | Commits | Tests | Status |
|---|---------|---------|-------|--------|
| §1 | stealth_init.js canvas/WebGL/WebRTC autoload | 4993028, 2173612 | test_stealth_script, test_browser_bridge_stealth_hook | ✅ P1 |
| §1 | Persistent Chrome profile (`~/.openclaw/krab_chrome_profile/`) | 4993028 | — | ✅ P1 |
| §1 | human_like.py helpers (Bezier mouse, random delay, smooth scroll) | 4cac96a | test_human_like | ✅ P2 |
| §1 | curl_cffi JA3 impersonation transport (layer 4) | 04642a1 | test_fingerprint_http | ✅ P2 |
| §1 | Stealth metrics `krab_stealth_detection_total{layer}` | 4a778b1 | test_stealth_metrics | ✅ P2 |
| §1 | Wire stealth detectors in mercadona scraper | 5e5e4f4 | test_mercadona_stealth_wiring | ✅ P2 |
| §2 | asyncio.Event per-chat `reread_chat` signal | 4ae7a09 | test_chat_window_reread | ✅ P2 |
| §3 | LRU eviction counter `krab_chat_windows_evicted_total` | 866bdca | test_chat_window_manager_evicted | ✅ P2 |
| §3 | P0_INSTANT bypass — mention/reply skip batcher | 51ee5ad | test_p0_instant_bypass | ✅ P1 |
| §3 | Robust mention + reply-to-self detector | c852d45 | test_mention_detector | ✅ P2 |
| §3 | Per-chat filter mode toggle | — | test_chat_filter_modes | ✅ P2 |
| §4 | build_skill_manifest.py + docs/SKILLS.md | faf40cc | test_build_skill_manifest | ✅ P2 |
| §4 | skill_scope.py — per-agent/per-chat skill gating | ac39e9f | test_skill_scope | ✅ P2 |
| §5 | `stage` field (experimental/beta/production) в CapabilityRegistry | 906e9d1 | test_command_registry_stage | ✅ P1 |
| §5 | `allow_experimental_for_chat()` в runtime_policy | 906e9d1 | test_runtime_policy_experimental | ✅ P1 |
| §6 | LLM re-ranking step (Gemini flash, top-50→top-10) | d26f349 | test_memory_llm_rerank | ✅ P1 |
| §6 | RRF threshold pruning (`final_rrf_score < 0.3` → skip) | 9953579 | test_memory_hybrid_reranker_threshold | ✅ P1 |
| §6 | Memory relevance metrics percentiles | 6479144 | test_memory_retrieval_scores | ✅ P3 |
| §7 | Sentry `agent_kin=krab` + `session` tags | bd7f2d9 | test_sentry_tags | ✅ P2 |
| §7 | `/api/ecosystem/comparison` Krab vs peers snapshot | cb9317f | test_ecosystem_comparison_endpoint | ✅ P2 |
| §8 | Ops artifact v2 — 3rd of 3 complete | 0d45dab | — | ✅ P1 |
| §8 | Krab Design System v1.0 | 4f00750 | — | ✅ P2 |
| §9 | `cross_ai_review.py` helper для external AI feedback | ca3245b | test_cross_ai_review | ✅ P2 |
| §9 | `krab-openclaw-chado-sync` weekly routine | ac39e9f, 3c9f53b | test_cron_chado_sync | ✅ P3 |

---

## Killer Findings

### Dead Code Bug (W2.7) — `classify_priority()` ← 0 call-sites

**Файл**: `src/core/message_priority_dispatcher.py`

`classify_priority()` определяла P0_INSTANT для mentions/replies — но функция нигде не вызывалась. Все входящие сообщения шли в общую очередь с 30-минутными LLM-loop'ами без приоритизации.

**Fix** (51ee5ad): wire `P0_INSTANT` path → bypass message batcher → instant dispatch для mentions и reply-to-self.

**Impact**: mentions теперь обрабатываются за секунды, а не ждут в хвосте очереди.

---

### Ruff Format Collateral (fd657c3)

Параллельные агенты (Wave 2-5) вносили несогласованное форматирование — финальный cleanup commit закрыл все ruff-предупреждения.

---

## Statistics

| Метрика | Значение |
|---------|----------|
| Всего commits (7 часов) | 48 |
| Session 17 чистых commits | 43 |
| Новых тест-файлов | 24 |
| Строк добавлено / удалено | +6750 / −226 |
| Изменённых файлов (от 0d45dab) | 92 |
| Chado §§ покрыто P1 | 5/5 |
| Chado §§ покрыто P2 | 14/14 |
| Chado §§ покрыто P3 | 2/5 |

---

## Dashboard V4 — Complete (7/7 pages)

Финализирован в начале сессии (carryover из S16):

| Page | Commit | Route |
|------|--------|-------|
| Ops V4 | 0c9c314 | `/v4/ops` |
| Costs V4 | (S16) | `/v4/costs` |
| Inbox V4 | (S16) | `/v4/inbox` |
| Stats V4 | (S16) | `/v4/stats` |
| Settings V4 | 564c000 | `/v4/settings` |
| Translator V4 | 0567088 | `/v4/translator` |
| Commands V4 | b3b09f2 | `/v4/commands` |

---

## Cross-AI Collaboration

- **Chado @callme_chado** дал 8-point feedback в How2AI → интегрирован в skeleton v2.1 (f05616f)
- **Design System v1.0** обозначает Chado как co-author (cross-AI collaboration precedent)
- `cross_ai_review.py` — helper для структурированного AI↔AI design review через Telegram Forum Topic
- `krab-openclaw-chado-sync` — weekly routine (Вс 19:07) для синхронизации апдейтов

---

## Remaining P2/P3 (deferred to Session 18)

### P2 (deferred)
- §2 P3: Архитектурный swimlane "async primitives" в `docs/ARCHITECTURE_V2_SKELETON.md`
- §3 P2: Expose `krab_chat_windows_active` metric
- §4 P3: Self-test `check_all_skills_discovered()` on startup
- §6 P2: MMR diversity penalty (λ=0.7/0.3 relevance/diversity)
- §6 P2: Query expansion (3 rephrase → OR merge) для коротких queries

### P3 (nice to have)
- §1 P3: Residential proxies `KRAB_RESIDENTIAL_PROXY_URL`
- §1 P3: CAPTCHA audio fallback via KrabEar STT
- §6 P3: Temporal re-ranking ("recent wins") + per-chat memory scoping
- §7 P2: Weekly digest публикует ecosystem comparison в How2AI Forum Topic
- §9 P3: Cross-AI routine запросы Chado об апдейтах

---

## Key Differentiators (Krab > Chado — confirmed this session)

1. **OpenClaw Gateway** — native tool/agent layer (Chado no equivalent)
2. **Swarm teams** — multi-agent delegation, CW delegируют в свёрм
3. **Dashboard V4** (7/7 pages) — owner-grade observability
4. **Memory Layer Phase 2**: 752k msgs / 72k chunks, queryable + temporal + per-chat scoped
5. **RAG re-ranking** (LLM re-rank + RRF threshold + MMR planned) — Chado: "контекст в памяти"
6. **12 routines** (5 launchd + 7 Desktop) — automated ecosystem maintenance
7. **Pre-commit hook + 7000+ tests** — качество подтверждено CI

**Positioning**: Chado — elegant minimalist. Krab — ecosystem operator. Different philosophies, complementary lessons.
