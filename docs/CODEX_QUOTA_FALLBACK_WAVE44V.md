# Wave 44-V — Codex quota detection + auto-fallback

## Что делает

Когда codex CLI subprocess (Wave 22-A) получает quota / rate-limit error
от ChatGPT API, Krab автоматически:

1. **Детектит quota error** в stderr/stdout через regex (`is_quota_error`).
2. **Помечает текущий account exhausted** в `codex_account_rotator` с
   соответствующим cooldown'ом (7 дней для weekly, 1 час для transient).
3. **Пробует следующий account** через `get_next_codex_home()` (LRU,
   filter available).
4. **Если ВСЕ accounts exhausted** — поднимает `CodexQuotaExhaustedError`,
   и openclaw_client fallback loop переключается на следующую модель из
   chain (e.g. `google/gemini-3-pro-preview` → `gemini-2.5-flash`).
5. **Уведомляет owner один раз** через `_send_proactive_watch_alert` при
   transition OUT-of-codex (debounced через
   `~/.openclaw/krab_runtime_state/codex_quota_state.json`).
6. **Recovery loop** раз в час проверяет, появились ли available accounts;
   если да — снимает codex_disabled флаг и шлёт recovery alert.

## Quota patterns

См. `src/integrations/codex_quota_state.py:CODEX_QUOTA_PATTERNS`. Совпадение
требует явного match хотя бы одного regex (не подстроки) — это исключает
false-positive trigger при медленных ответах:

- `rate[\s_-]?limit[\s_-]?exceeded`
- `quota[\s_-]?(exhausted|exceeded|reached)`
- `\b429\b`
- `insufficient[\s_-]?quota`
- `weekly[\s_-]?(limit|quota)`
- `RateLimitError`
- `refresh[\s_-]?token[\s_-]?reused` (OAuth — also blocks)
- `You exceeded your current quota`
- ...

Weekly indicators (для cooldown classification): `weekly`, `7-day`, `week cap`.

## Cooldowns

| Kind | Duration | Когда |
|------|----------|-------|
| `weekly` | 7 дней | Stderr содержит "weekly", "7-day" и т.п. |
| `transient` | 1 час | Любой другой quota / rate-limit |

Recovery probe loop проверяет каждый час → transient восстанавливается на
следующей итерации, weekly — через ~7d.

## Fallback chain

Configured в `~/.openclaw/agents/main/agent/models.json`:

```
codex-cli/gpt-5.5  (primary)
google/gemini-3-pro-preview  (fallback 1)
google/gemini-2.5-pro-preview
google/gemini-2.5-flash
LM Studio local  (last resort)
```

Когда CodexQuotaExhaustedError поднимается:
- Outer 4-attempt loop в `openclaw_client._openclaw_completion_once`
  переключает `attempt_model` на следующую модель из
  `get_runtime_fallback_models()`.
- Запись `model_fallback_engaged from=... to=... reason=quota` в логах.

## Observability

Логи (structlog):
- `codex_quota_detected provider=codex-cli/gpt-5.5 account=primary kind=weekly`
- `codex_all_accounts_exhausted` — все accounts помечены exhausted
- `model_fallback_engaged from=codex-cli/gpt-5.5 to=google/gemini-3-pro-preview reason=quota`
- `codex_disabled_transition` — owner alert отправлен (transition)
- `codex_recovered_transition` — recovery alert отправлен

API endpoints:
- `GET /api/model/status` → `codex_accounts_exhausted: true|false`

State files:
- `~/.openclaw/krab_runtime_state/codex_accounts.json` — per-account state
- `~/.openclaw/krab_runtime_state/codex_quota_state.json` — transition state

## Owner notification

Debounced — alert отправляется только при transition (raз на quota window):

- **OUT**: `⚠️ Codex квота исчерпана для всех accounts (weekly).
  Переключился на google/gemini-3-pro-preview. Auto-recovery когда квота
  сбросится.`
- **IN (recovery)**: `✅ Codex восстановлен — primary вернулся к
  codex-cli/* (доступно accounts: 2).`

## Manual ops

```bash
# Проверить state аккаунтов
cat ~/.openclaw/krab_runtime_state/codex_accounts.json | python3 -m json.tool

# Проверить transition state
cat ~/.openclaw/krab_runtime_state/codex_quota_state.json | python3 -m json.tool

# Сбросить quota state (force re-enable codex)
echo '{}' > ~/.openclaw/krab_runtime_state/codex_quota_state.json
echo '{}' > ~/.openclaw/krab_runtime_state/codex_accounts.json

# Проверить через API
curl -sS http://127.0.0.1:8080/api/model/status | python3 -c \
  "import sys,json; d=json.load(sys.stdin); print('codex_exhausted=', d.get('codex_accounts_exhausted'))"
```

## Tests

`tests/unit/test_codex_quota_fallback_wave44v.py` — pattern detection,
classification, transition state idempotency, account rotation flow.

```bash
venv/bin/pytest tests/unit/test_codex_quota_fallback_wave44v.py -q
```
