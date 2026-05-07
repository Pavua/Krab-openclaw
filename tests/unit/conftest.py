# -*- coding: utf-8 -*-
"""
conftest.py — unit-level guard rails для userbot inbox side effects.

Что это:
- автоподмена inbox-capture в большинстве unit-тестов `userbot_bridge`;
- исключение только для тестов, которые специально проверяют новый inbox flow.

Зачем нужно:
- после добавления `incoming owner request/mention -> inbox` старые unit-тесты
  начали писать в живой per-account inbox-state;
- нам нужна изоляция тестов без ручного патча каждого legacy файла, особенно
  когда часть файлов сейчас принадлежит другой macOS-учётке.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

import src.config as _config_module
from src.userbot_bridge import KraabUserbot


@pytest.fixture(autouse=True)
def _align_config_after_reload(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """
    Гарантирует, что mixin lazy-imports (`from ..config import config`)
    видят тот же Config-объект, что и `userbot_bridge.config`.

    Проблема: test_config_voice_settings.py делает `importlib.reload(src.config)`,
    после чего `src.config.config` — НОВЫЙ экземпляр, а `userbot_bridge.config`
    всё ещё ссылается на СТАРЫЙ. Mixin-методы (access_control, voice_profile)
    делают lazy `from ..config import config` → получают новый, тесты патчат
    старый → fails.

    Фикс: перед каждым тестом форсируем `src.config.config` = тот объект,
    который держит userbot_bridge. Тогда любой monkeypatch на него виден везде.
    """
    import src.userbot_bridge as _ub

    canonical = _ub.config
    canonical_cls = type(canonical)
    if _config_module.config is not canonical:
        monkeypatch.setattr(_config_module, "config", canonical)
    # Wave 12: после importlib.reload(src.config) в test_config_voice_settings.py
    # _config_module.Config — это НОВЫЙ класс, а singleton `canonical` —
    # экземпляр СТАРОГО класса. Тесты, делающие `from src.config import Config`
    # и затем `Config.X = ...`, мутируют новый класс, а singleton читает старый.
    # Восстанавливаем `_config_module.Config` к классу singleton.
    if _config_module.Config is not canonical_cls:
        monkeypatch.setattr(_config_module, "Config", canonical_cls)
    yield


@pytest.fixture(autouse=True)
def isolate_userbot_inbox_capture(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> Iterator[None]:
    """
    Отключает inbox-capture в unit-тестах по умолчанию.

    Оставляем живой capture только там, где он и является предметом проверки.
    """
    node_path = str(getattr(request.node, "fspath", "") or "")
    if node_path.endswith("test_userbot_inbox_flow.py") or node_path.endswith(
        "test_userbot_reply_trace_flow.py"
    ):
        yield
        return

    monkeypatch.setattr(
        KraabUserbot,
        "_sync_incoming_message_to_inbox",
        lambda self, **kwargs: {"ok": False, "skipped": True, "reason": "unit_test_isolation"},
        raising=False,
    )
    yield


# ---------------------------------------------------------------------------
# Session 39: persistent runtime state isolation
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_persistent_runtime_state(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    """Перенаправляет файлы, которые production runtime пишет в ~/.openclaw/,
    на временную директорию для каждого теста.

    Зачем: parallel xdist runs (`-n 4`) видят production `runs.sqlite`,
    `bypass_perf.jsonl` и т.п. Через poll_active_tasks() тесты замечают
    реальные задачи (e.g. "Nightly Self-Diagnostics" stale 264s) и LLM
    watchdog отменяет тестовые requests.

    Стратегия: точечный redirect known leak paths в tmp dir. Не делаем
    глобальный Path.home() patch — это сломает много модулей которые
    легитимно нуждаются в реальном home.
    """
    tmp_root = tmp_path_factory.mktemp("krab_test_state")

    # 1. RUNS_DB_PATH — openclaw task tracker (главный источник watchdog cancel'ов)
    try:
        from src.core import openclaw_task_poller as _otp

        monkeypatch.setattr(_otp, "RUNS_DB_PATH", tmp_root / "runs.sqlite")
    except Exception:  # noqa: BLE001
        pass

    # 2. bypass_perf.jsonl — leak attempts/failures между тестами
    try:
        from src.integrations import _bypass_perf as _bp

        monkeypatch.setattr(_bp, "PERF_LOG", tmp_root / "bypass_perf.jsonl")
    except Exception:  # noqa: BLE001
        pass

    # 3. _rerank_cache OrderedDict — module-level singleton, leak'ает между
    # parallel workers (test_memory_adaptive_rerank_llm видит cached entries
    # от других тестов и принимает решения skip-llm-rerank по ним).
    try:
        from src.core import memory_llm_rerank as _mr

        _mr._rerank_cache.clear()
    except Exception:  # noqa: BLE001
        pass

    # 5. swarm_channels.json — production singleton конфиг forum/legacy/
    # additional broadcast chats. Тесты writing'или сюда test placeholders
    # (-100, -200) которые после reboot Krab loadit как production config →
    # `!swarm` в group не работает. Session 40: redirect _STATE_PATH в tmp,
    # сбрасываем in-memory кэш SwarmChannels чтобы каждый тест получил
    # чистый state.
    try:
        from src.core import swarm_channels as _sc

        # Redirect persistent file
        monkeypatch.setattr(_sc, "_STATE_PATH", tmp_root / "swarm_channels.json")
        # Reset module-level singleton's in-memory state (если уже создан)
        singleton = getattr(_sc, "swarm_channels", None)
        if singleton is not None:
            for attr in ("_team_topics", "_team_chats", "_additional_chats"):
                store = getattr(singleton, attr, None)
                if hasattr(store, "clear"):
                    store.clear()
            if hasattr(singleton, "_forum_chat_id"):
                singleton._forum_chat_id = None
    except Exception:  # noqa: BLE001
        pass

    # 6. OBSIDIAN_VAULT — hardcoded path /Users/pablito/Documents/Obsidian Vault.
    # memo_service пишет туда — параллельные workers конкурируют за один файл.
    try:
        from src.core import memo_service as _memo

        monkeypatch.setattr(_memo, "OBSIDIAN_VAULT", tmp_root / "obsidian_vault")
    except Exception:  # noqa: BLE001
        pass

    yield

    # Post-test: ещё раз чистим LRU чтобы следующий test видел пустой cache
    try:
        from src.core import memory_llm_rerank as _mr_post

        _mr_post._rerank_cache.clear()
    except Exception:  # noqa: BLE001
        pass
