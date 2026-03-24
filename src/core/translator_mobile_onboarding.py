# -*- coding: utf-8 -*-
"""
translator_mobile_onboarding.py — onboarding packet для iPhone companion.
"""

from __future__ import annotations
from pathlib import Path
from typing import Any


def _helper(project_root: Path | None, name: str) -> dict[str, Any]:
    """Возвращает helper-card для onboarding packet."""
    root = Path(project_root or Path.cwd())
    path = root / name
    return {
        "label": name.replace(".command", ""),
        "path": str(path),
        "exists": path.exists(),
    }


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
    runtime_payload = dict(runtime_lite or {})
    readiness = dict(translator_readiness or {})
    control = dict(control_plane or {})
    mobile = dict(mobile_readiness or {})
    delivery = dict(delivery_matrix or {})
    preflight = dict(live_trial_preflight or {})
    summary = dict(mobile.get("summary") or {}) if isinstance(mobile.get("summary"), dict) else {}
    actions = dict(mobile.get("actions") or {}) if isinstance(mobile.get("actions"), dict) else {}
    ordinary_calls = dict(delivery.get("ordinary_calls") or {}) if isinstance(delivery.get("ordinary_calls"), dict) else {}

    preflight_status = str(preflight.get("status") or "blocked").strip() or "blocked"
    mobile_status = str(mobile.get("status") or "unknown").strip() or "unknown"
    ordinary_status = str(ordinary_calls.get("status") or "blocked").strip() or "blocked"
    selected_device_id = str((mobile.get("devices") or {}).get("selected_device_id") or "")

    # Даже если общий preflight пока заблокирован внешними условиями вроде
    # shared-workspace discipline, сам onboarding packet не должен объявляться
    # полностью "blocked", когда companion уже зарегистрирован/привязан и
    # ordinary-call path можно продолжать собирать дальше.
    if preflight_status == "ready_for_trial":
        status = "trial_ready"
    elif mobile_status == "bound" and ordinary_status in {"device_ready", "trial_ready"}:
        status = "ready_for_onboarding"
    elif mobile_status in {"registered", "attention", "bound"}:
        status = "ready_for_onboarding"
    elif mobile_status == "not_configured":
        status = "awaiting_companion"
    else:
        status = "blocked"

    subtitles_ready = ordinary_status in {"device_ready", "trial_ready"}
    ru_es_ready = bool(
        ordinary_status in {"device_ready", "trial_ready"}
        and str(((control.get("runtime_policy") or {}).get("language_pair")) or "ru-es") in {"ru-es", "es-ru"}
    )
    trial_profiles = [
        {
            "id": "subtitles_first",
            "label": "Subtitles First",
            "status": "ready" if subtitles_ready else "blocked",
            "reason": "Самый безопасный стартовый профиль для daily-use companion." if subtitles_ready else "Нужен хотя бы registered/bound companion.",
        },
        {
            "id": "voice_first_guarded",
            "label": "Voice First Guarded",
            "status": "blocked",
            "reason": "Voice-first пока не считается daily-use safe и остаётся позже subtitles-first.",
        },
        {
            "id": "ru_es_duplex",
            "label": "RU-ES Duplex",
            "status": "ready" if ru_es_ready else "blocked",
            "reason": "Целевой языковой профиль для Испании." if ru_es_ready else "Нужна активная RU-ES session/policy truth.",
        },
    ]

    recommended_trial_profile = "subtitles_first"
    if not subtitles_ready and ru_es_ready:
        recommended_trial_profile = "ru_es_duplex"

    draft_defaults = dict(actions.get("draft_defaults") or {}) if isinstance(actions.get("draft_defaults"), dict) else {}
    trial_prep_payload = {
        "device_id": selected_device_id,
        "source": "mobile",
        "translation_mode": str(((control.get("runtime_policy") or {}).get("translation_mode")) or "ru_es_duplex"),
        "notify_mode": str(((control.get("runtime_policy") or {}).get("notify_mode")) or "auto_on"),
        "tts_mode": str(((control.get("runtime_policy") or {}).get("tts_mode")) or "hybrid"),
        "src_lang": str(((control.get("runtime_policy") or {}).get("source_lang")) or "auto"),
        "tgt_lang": str(((control.get("runtime_policy") or {}).get("target_lang")) or "ru"),
        "label": "Companion Trial",
        **draft_defaults,
    }

    return {
        "ok": True,
        "status": status,
        "ready": status == "trial_ready",
        "summary": {
            "mobile_status": mobile_status,
            "ordinary_calls_status": ordinary_status,
            "registered_devices": int(summary.get("registered_devices") or 0),
            "bound_devices": int(summary.get("bound_devices") or 0),
            "selected_device_id": selected_device_id,
        },
        "install_tracks": [
            {
                "id": "xcode_free_signing",
                "label": "Xcode Free Signing",
                "status": "recommended",
                "detail": "Основной debug/install path без paid Apple Developer.",
            },
            {
                "id": "altstore_sidestore",
                "label": "AltStore / SideStore",
                "status": "daily_use",
                "detail": "Предпочтительный повседневный путь после локальной сборки companion.",
            },
        ],
        "trial_profiles": trial_profiles,
        "packet_preview": {
            "recommended_trial_profile": recommended_trial_profile,
            "trial_prep_payload": trial_prep_payload,
            "next_step": str((preflight.get("actions") or {}).get("next_step") or "register_companion"),
        },
        "helpers": {
            "build_packet": _helper(project_root, "Build Translator Mobile Onboarding Packet.command"),
            "prepare_xcode_project": _helper(project_root, "Prepare iPhone Companion Xcode Project.command"),
            "open_companion_skeleton": _helper(project_root, "Open iPhone Companion Skeleton.command"),
            "start_full_ecosystem": _helper(project_root, "Start Full Ecosystem.command"),
        },
        "blockers": [
            str(item).strip()
            for item in (preflight.get("blockers") or [])
            if str(item or "").strip()
        ],
        "warnings": [
            str(item).strip()
            for item in (preflight.get("warnings") or [])
            if str(item or "").strip()
        ],
        "runtime_lite": runtime_payload,
        "translator_readiness": readiness,
        "control_plane": control,
        "mobile_readiness": mobile,
        "delivery_matrix": delivery,
        "live_trial_preflight": preflight,
    }
