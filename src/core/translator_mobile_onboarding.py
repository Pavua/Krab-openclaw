# -*- coding: utf-8 -*-
"""
translator_mobile_onboarding.py — onboarding packet для iPhone companion.
"""

from __future__ import annotations
from pathlib import Path
from typing import Any


def build_translator_mobile_onboarding_packet(
    *,
    project_root: Path | None = None,
    runtime_lite: dict[str, Any] | None = None,
    translator_readiness: dict[str, Any] | None = None,
    control_plane: dict[str, Any] | None = None,
    mobile_readiness: dict[str, Any] | None = None,
    delivery_matrix: dict[str, Any] | None = None,
    live_trial_preflight: dict[str, Any] | None = None,
    signal_log: Path | None = None,
    current_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Собирает truthful onboarding packet для реального выхода на iPhone companion."""
    return {
        "ready": False,
        "blockers": ["onboarding_module_stub"],
        "warnings": [],
        "steps": [],
        "runtime_lite": runtime_lite or {},
        "translator_readiness": translator_readiness or {},
        "control_plane": control_plane or {},
        "mobile_readiness": mobile_readiness or {},
        "delivery_matrix": delivery_matrix or {},
        "live_trial_preflight": live_trial_preflight or {},
    }
