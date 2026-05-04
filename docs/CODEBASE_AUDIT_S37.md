# Codebase Audit — Session 37 / Wave 20-C

Дата: 2026-05-04
Ветка: claude/naughty-ellis-f5a58e (worktree от main HEAD b619610)

## Итог

| Проверка | Результат |
|---|---|
| `get_hermes_bridge_sync` callsites в src/ | 0 (только тесты) → добавлен DeprecationWarning |
| Wave 18-F debug code (`Wave 18-F`, `debug_bypass_check`) | 0 остатков ✓ |
| Wave 14-D `_codex_first_chunk_cap_sec` | Активно используется — оставлен |
| Stale env keys (`OLD_`, `DEPRECATED_`, `TEMP_`, `TEST_`) | 0 в .env ✓ |
| pytest.skip без reason/link | 0 — все задокументированы |

**LOC удалено: 0** (только добавлен `DeprecationWarning`).

---

## 1. `get_hermes_bridge_sync` — DeprecationWarning добавлен

**Файл:** `src/integrations/hermes_acp_bridge.py` (строка ~278)

**Ситуация:** Wave 16-P мигрировал все production callsites на async `await get_hermes_bridge()`.
Синхронная версия `get_hermes_bridge_sync()` осталась как backward-compat shell.

**Поиск callsites в `src/`:** 0 — функция используется **только в тестах**:
- `tests/unit/test_wave16p_fixes.py:77` — тест самой deprecated функции
- `tests/unit/test_hermes_acp_bridge.py:21,167,168` — тест singleton идемпотентности

**Действие:** добавлен `warnings.warn(DeprecationWarning, stacklevel=2)` в тело функции.
Функция **не удалена** — тесты продолжают проходить, DeprecationWarning появится в test output.

**TODO Session 38+:** удалить функцию + обновить тесты (заменить на `asyncio.run(get_hermes_bridge())`).

---

## 2. Wave 18-F debug code — чисто

**Файл:** `src/openclaw_client.py`

Поиск `"Wave 18-F"` и `"debug_bypass_check"` вернул 0 совпадений.
Wave 18-G корректно удалил debug-код. Чисто.

---

## 3. Wave 14-D `_codex_first_chunk_cap_sec` — оставлен (нужен)

**Файл:** `src/userbot/llm_flow.py` (строки 906, 1002, 1006, 1013, 1031, 1053)

Wave 16-I (idle-aware hang detection) и Wave 19-D (psutil liveness) дополняют,
но **НЕ заменяют** Wave 14-D:

- Wave 14-D срабатывает когда **ни одного chunk** не пришло за `_codex_first_chunk_cap_sec` секунд
- Wave 16-I/19-D работают на стадии idle-between-chunks (уже есть partial output)

Эти механизмы ортогональны. Удалять нельзя.

---

## 4. Stale env keys — нет

```bash
grep -E "^(OLD_|DEPRECATED_|TEMP_|TEST_)" .env
# → 0 совпадений
```

---

## 5. Skipped тесты — все задокументированы

Все найденные `pytest.skip` имеют явный reason-string. Три категории:

### А. Module-level skip с задокументированным reason (OK):

| Файл | Reason |
|---|---|
| `test_response_hard_cap_subtask_aware.py` | Wave 14-K удалил internal symbols `_classify_tool_subtask_kind` / `_detect_subtask_success_in_tool_calls`; coverage перенесена в `test_codex_cli_fallback_wiring.py` |
| `test_krab_ear_watchdog.py` | `scripts.krab_ear_watchdog` недоступен в test env |
| `test_core_mcp_registry.py` | `src.core.mcp_registry` недоступен в test env |
| `test_check_current_account_runtime.py` | `scripts.check_current_account_runtime` недоступен |
| `test_sync_gemini_cli_oauth.py` | `scripts.sync_gemini_cli_oauth` недоступен |

### Б. `@pytest.mark.skip` с reason (OK, но backlog):

| Файл | Строки | Reason |
|---|---|---|
| `test_memory_mmr.py` | 130, 217 | Wave 11 refactor: `HybridRetriever._materialize_results` изменён, тесты obsolete. Ждут sync с новым debug_calls API |

**TODO Session 38+:** обновить или удалить `test_memory_mmr.py` строки 130-240 (2 теста).

### В. Conditional skip по env (OK — runtime-зависимые):
- `test_wave_16i_idle_liveness.py` — env override check
- `test_memory_retrieval.py` — sqlite_vec недоступен
- `test_llm_retry.py` — httpx не установлен
- `test_web_app_dashboard_endpoints.py` — WEB_API_KEY не задан
- `test_config_module.py` — VOICE_REPLY_BLOCKED_CHATS задан

---

## TODO для Session 38+

1. **Удалить `get_hermes_bridge_sync()`** из `src/integrations/hermes_acp_bridge.py`
   — обновить тесты: `test_wave16p_fixes.py`, `test_hermes_acp_bridge.py`
2. **Обновить/удалить** 2 skipped теста в `tests/unit/test_memory_mmr.py`
   — Wave 11 refactor сделал их obsolete; нужна новая проверка под актуальный API
3. **Рассмотреть** удаление `test_response_hard_cap_subtask_aware.py`
   — полностью module-skipped, coverage покрыта `test_codex_cli_fallback_wiring.py`
