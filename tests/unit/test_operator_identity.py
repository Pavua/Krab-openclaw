# -*- coding: utf-8 -*-
"""
Тесты operator identity foundation.

Покрываем:
1) стабильный `account_id` для одной и той же macOS-учётки;
2) fallback на имя домашней директории, если `USER` пустой;
3) детерминированный `trace_id` для одинакового набора частей.
"""

from __future__ import annotations

from pathlib import Path

import src.core.operator_identity as identity_module


def test_current_account_id_is_stable_for_same_home(monkeypatch, tmp_path: Path) -> None:
    """Для одной и той же учётки account-id должен быть стабильным."""
    monkeypatch.setenv("USER", "USER2")
    monkeypatch.setattr(identity_module.Path, "home", classmethod(lambda cls: tmp_path))

    first = identity_module.current_account_id()
    second = identity_module.current_account_id()

    assert first == second
    assert len(first) == 12


def test_current_operator_id_falls_back_to_home_dir_name(monkeypatch, tmp_path: Path) -> None:
    """Если USER пустой, operator-id берётся из имени домашней директории."""
    monkeypatch.delenv("USER", raising=False)
    home_path = tmp_path / "operator-home"
    monkeypatch.setattr(identity_module.Path, "home", classmethod(lambda cls: home_path))

    assert identity_module.current_operator_id() == "operator-home"


def test_build_trace_id_is_deterministic() -> None:
    """Одинаковые входы должны давать одинаковый trace-id."""
    first = identity_module.build_trace_id("watch", "gateway_down", "2026-03-12T10:00:00+00:00")
    second = identity_module.build_trace_id("watch", "gateway_down", "2026-03-12T10:00:00+00:00")
    third = identity_module.build_trace_id("watch", "gateway_down", "2026-03-12T10:05:00+00:00")

    assert first == second
    assert first.startswith("watch:")
    assert first != third
