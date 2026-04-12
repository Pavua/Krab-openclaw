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
    if _config_module.config is not canonical:
        monkeypatch.setattr(_config_module, "config", canonical)
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
