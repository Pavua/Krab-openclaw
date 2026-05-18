# Phase 4: Confidence-Gated Routing

Design document for the next iteration of local vs cloud LLM routing.
Status: **draft**, awaiting Phase 3 verifier sample accumulation (S65 W8).

## 1. Context

Local-first routing has rolled out in stages:

- **Phase 1 — vision** (production-verified): OCR / image understanding routed к
  local MLX где возможно.
- **Phase 2 — translator** (production-verified): translator path uses local
  `gemini-3-flash-preview` as preferred speed primary.
- **Phase 2.5 — audio** (production-verified): voice transcription / TTS local
  по умолчанию.
- **Phase 3 — verifier** (S57 P3.1 + S58 dashboard): 20% sampling of local primary
  bypass responses, divergence scored against cloud reference. Live, but waiting
  on **S65 W1 silent-death fix** before samples reliably flow.

Current state: **~8% of LLM traffic** is local (S60 measurement). Cloud остаётся
primary для chat / reasoning / code generation. Verifier dashboard
(`/api/admin/local-draft-verifier-stats`) — единственный пока сигнал, можно ли
расширять local share без quality regression.

## 2. Decision Tree

После того как `/api/admin/local-draft-verifier-stats` accumulate **≥50 samples**:

| Median divergence score | Action |
|------------------------:|--------|
| **<5%** | Expand local share к **30%**, monitor 3 days, then **50%** if стабильно. Sample rate можно снизить к 10%. |
| **5–15%** | Maintain at current **~8%**. Sample rate stays at **20%**. Investigate top-divergence task types. |
| **>15%** | **Narrow** local scope. Disable local routing для task types с highest divergence. Sample rate stays 20% для оставшихся. |

Boundaries — tentative; revisit после первой полной histogram review.

## 3. Per-Task-Type Granularity

Aggregate scores при гранулярности `task_type` — initial split:

- **Likely safe for local** (short, deterministic): Q&A short, translation,
  summarization (<2k tokens), formatting normalization, simple extraction.
- **Likely cloud-only** (long horizon, reasoning-heavy): code generation,
  multi-step planning, long-context summarization (>8k tokens), tool use loops.
- **Ambiguous** (depends on prompt): chat responses, classification, rerank
  scoring.

Verifier должен tag each sample с `task_type`, чтобы dashboard breakdown был
actionable. Per-task histogram → per-task decision tree, не глобальный единый
threshold.

## 4. Implementation Strategy

1. **Env vars per task type** controlling local share:
   ```bash
   KRAB_LOCAL_SHARE_TRANSLATION=1.0      # already
   KRAB_LOCAL_SHARE_QA_SHORT=0.3         # new
   KRAB_LOCAL_SHARE_SUMMARIZATION=0.5    # new
   KRAB_LOCAL_SHARE_CODEGEN=0.0          # cloud-only
   ```
   Default — текущий behavior (~8% combined).

2. **A/B framework**: route 50% к local, 50% к cloud для same prompts (sampled
   subset), feed both responses в verifier as **mutual comparison**, not
   one-way reference. Identifies cases где local is *better* than cloud.

3. **Automatic backoff**: rolling 1h window median divergence > threshold →
   throttle local share для that task_type by 50%. Telegram alert.

4. **Manual override** через owner panel `/admin/routing` — pinning task type
   к local / cloud / auto.

## 5. Risk Analysis

- **Verifier accuracy** — divergence score uses cloud as reference, assuming
  cloud is "ground truth". Risky для tasks где cloud sometimes wrong. Mitigation:
  manual review of top-50 high-divergence samples за first week.
- **Cost trade-off** — cloud cost trending down (Wave 66 Vertex bonus credits до
  2027-03). Local infra cost stable (electricity + LM Studio RAM). At <30% local
  share, savings marginal; >50% — meaningful.
- **Latency** — local fast for short prompts (~200-500ms TTFT), но cloud Gemini
  3 Flash сейчас competitive (~600-900ms). Latency не main driver; quality and
  cost are.
- **Quota fragility** — over-reliance на local hides cloud quota issues. Keep
  shadow cloud probe (1% sample) даже при 50% local.

## 6. Migration Path

| Step | Action | Gate |
|------|--------|------|
| 1 | Apply **S65 W1 silent-death fix** | verifier samples flow stable |
| 2 | Accumulate **50+ samples** в `/api/admin/local-draft-verifier-stats` | dashboard shows divergence histogram |
| 3 | Decision per §2 tree (global + per-task §3) | owner review |
| 4 | Implement **env-based per-task local share** (§4.1) | config rollout |
| 5 | Monitor **1 week** — Sentry, divergence rolling median, cost dashboard | weekly digest |
| 6 | Iterate (expand / hold / rollback) | continue or revert per metrics |

Rollback knob: set all `KRAB_LOCAL_SHARE_*=0` → instant return к 100% cloud
(translator / vision / audio paths stay local — those не affected).

## 7. Open Questions

- Should verifier sample rate adapt automatically (e.g. lower при stable low
  divergence)?
- Per-chat overrides (some chats more latency-sensitive)?
- Cross-validate divergence score с user feedback signal (deletions, reactions
  via `feedback_tracker`)?

---

**Owner**: Krab core routing.
**Next review**: после Step 2 (50+ samples accumulated).
**Linked**: S57 P3.1 (verifier), S58 (dashboard), S60 (local share measurement),
S65 W1 (silent-death fix), `docs/SMART_ROUTING_DESIGN.md`.
