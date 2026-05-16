# Session 53 — OptiQ JIT-load disabled

## Summary

OptiQ модель (`gemma-4-26b-a4b-it-optiq`) больше не auto-load'ится в LM Studio :1234
из-за **cleared runtime cache**.

## Root cause (S53 subagent investigation)

`~/.openclaw/krab_runtime_state/mlx_local_aliases_runtime.json` содержал:
```json
{
  "ts": 1778882605.550955,
  "aliases": {
    "mlx-local-kv4/gemma-4-26b-a4b-it-optiq-4bit":
      "/Volumes/4TB SSD/LMStudio_models/mlx-community/gemma-4-26B-A4B-it-OptiQ-4bit"
  }
}
```

Это был **stale cache** от Wave 222/223 RotorQuant `mlx_local_discovery` scan.
Когда какой-то client делал HTTP request к LM Studio с `model="gemma-4-26b-a4b-it-optiq"`,
LM Studio **JIT-load'ила** OptiQ-4bit (15 GB) дополнительно к Gemma vanilla — dual-load
30 GB RAM → kernel panic risk на 36 GB M4 Max.

## Fix

`mv ~/.openclaw/krab_runtime_state/mlx_local_aliases_runtime.json ...bak_session53`

Cache removed. Krab restart не вернёт его (только при explicit `mlx_local_discovery.scan()`
который сейчас не запущен — RotorQuant launchd `com.user.mlx-lm-server` unloaded в S52).

## Sources investigated (clean)

- `crontab -l` — empty (only echo test)
- `com.user.mlx-lm-server` plist — uses :8088, not :1234
- `coexistence-monitor`, `health-watcher` — no LM Studio probes
- Krab `active_model.json` = `codex-cli/gpt-5.5` (правильно)
- `KRAB_LOCAL_VISION_MODEL=gemma-4-26b-a4b-it@4bit` — NOT OptiQ

## Hardcoded references (still in code, не убираем сейчас)

- `src/core/mlx_local_aliases.py:44` — static alias map (intentional reference)
- `src/modules/web_routers/models_admin_router.py:964` — UI admin Load button (manual click only)

Эти entries безопасны — они НЕ вызывают JIT auto-load. Только runtime cache был источником.

## Verification

- LM Studio `lms ps` should show ONLY `krab-vision-primary` (Gemma vanilla)
- No `gemma-4-26b-a4b-it-optiq` в loaded models
