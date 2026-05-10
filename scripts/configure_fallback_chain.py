#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Wave 54-A: Cross-vendor fallback chain configurator.

Скрипт читает ~/.openclaw/openclaw.json и предлагает обновлённую fallback-цепочку
с чередованием Google и Anthropic-Vertex моделей для защиты от single-vendor outage.

ВАЖНО: скрипт НЕ изменяет конфиг автоматически — выводит preview и копирует
патч в буфер обмена (если pbcopy доступен). Owner применяет изменение вручную.

Использование:
    python3 scripts/configure_fallback_chain.py          # preview
    python3 scripts/configure_fallback_chain.py --apply  # применить к runtime config
    python3 scripts/configure_fallback_chain.py --check  # только проверить текущую цепочку
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

_RUNTIME_CONFIG_PATH = Path.home() / ".openclaw" / "openclaw.json"
_MODELS_JSON_PATH = Path.home() / ".openclaw" / "agents" / "main" / "agent" / "models.json"

# Рекомендуемая cross-vendor fallback цепочка (Wave 54-A).
# Anthropic-Vertex вставлены после первых 1-2 Google-попыток — при Google-outage
# Krab переключится на Claude после 1 провала, а не после 7.
RECOMMENDED_FALLBACKS: list[str] = [
    "google-gemini-cli/gemini-3-pro-preview",
    "anthropic-vertex/claude-opus-4-6",  # cross-vendor anchor: Claude после 1-й Google ошибки
    "google-vertex/gemini-3-pro-preview",
    "anthropic-vertex/claude-sonnet-4-6",  # fast Anthropic fallback
    "google-vertex/gemini-flash-latest",
    "google-gemini-cli/gemini-2.5-pro",
    "google-gemini-cli/gemini-3-flash-preview",
    "google-gemini-cli/gemini-2.5-flash",
]

# Минимальный набор моделей без которых цепочка неполноценна
_REQUIRED_ANTHROPIC = {"anthropic-vertex/claude-opus-4-6", "anthropic-vertex/claude-sonnet-4-6"}


def _read_config() -> dict[str, Any]:
    """Читает ~/.openclaw/openclaw.json."""
    if not _RUNTIME_CONFIG_PATH.exists():
        print(f"[ERROR] Конфиг не найден: {_RUNTIME_CONFIG_PATH}", file=sys.stderr)
        sys.exit(1)
    try:
        return json.loads(_RUNTIME_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"[ERROR] Не удалось прочитать конфиг: {exc}", file=sys.stderr)
        sys.exit(1)


def _get_current_fallbacks(payload: dict[str, Any]) -> list[str]:
    """Извлекает текущий список fallback-моделей из конфига."""
    agents = payload.get("agents") or {}
    defaults = (agents.get("defaults") or {}) if isinstance(agents, dict) else {}
    model = (defaults.get("model") or {}) if isinstance(defaults, dict) else {}
    fallbacks = model.get("fallbacks") or [] if isinstance(model, dict) else []
    return [str(f).strip() for f in fallbacks if str(f or "").strip()]


def _get_anthropic_vertex_models_in_runtime() -> list[str]:
    """Возвращает anthropic-vertex модели, реально сконфигурированные в models.json."""
    if not _MODELS_JSON_PATH.exists():
        return []
    try:
        data = json.loads(_MODELS_JSON_PATH.read_text(encoding="utf-8"))
        providers = data.get("providers") or {}
        av_section = providers.get("anthropic-vertex") or {}
        models_list = av_section.get("models") or []
        return [f"anthropic-vertex/{m['id']}" for m in models_list if m.get("id")]
    except (OSError, ValueError, KeyError, TypeError):
        return []


def _check_chain(current: list[str]) -> None:
    """Проверяет текущую цепочку на наличие cross-vendor protection."""
    print("=== Текущая fallback-цепочка ===")
    for i, m in enumerate(current, 1):
        vendor = (
            "anthropic"
            if "anthropic" in m
            else "google"
            if "google" in m or "vertex" in m
            else "other"
        )
        print(f"  {i}. {m}  [{vendor}]")

    anthropic_in_chain = [m for m in current if "anthropic" in m]
    google_in_chain = [
        m for m in current if "google" in m or ("vertex" in m and "anthropic" not in m)
    ]

    print(f"\nGoogle моделей: {len(google_in_chain)}")
    print(f"Anthropic моделей: {len(anthropic_in_chain)}")

    if not anthropic_in_chain:
        print("\n⚠️  РИСК: Все модели — Google. При Google-outage все 7 попыток провалятся.")
        print("   Запусти скрипт без --check для просмотра рекомендации.")
    else:
        # Найти позицию первого Anthropic
        first_anthropic_pos = next(
            (i for i, m in enumerate(current) if "anthropic" in m), len(current)
        )
        if first_anthropic_pos >= 3:
            print(
                f"\n⚠️  Первый Anthropic — позиция {first_anthropic_pos + 1}. "
                "Рекомендуется переместить на позицию 2."
            )
        else:
            print(
                f"\n✅ Cross-vendor цепочка OK (первый Anthropic на позиции {first_anthropic_pos + 1})."
            )


def _print_recommended(primary: str) -> None:
    """Выводит рекомендуемую цепочку."""
    print("\n=== Рекомендуемая fallback-цепочка (Wave 54-A) ===")
    for i, m in enumerate(RECOMMENDED_FALLBACKS, 1):
        vendor = "anthropic" if "anthropic" in m else "google"
        marker = " ← NEW" if "anthropic" in m else ""
        print(f"  {i}. {m}  [{vendor}]{marker}")
    print(f"\nPrimary: {primary} (без изменений)")


def _build_patch(_payload: dict[str, Any]) -> dict[str, Any]:
    """Строит патч для секции agents.defaults.model.fallbacks."""
    # Обновляем только fallbacks, primary не трогаем
    return {"fallbacks": RECOMMENDED_FALLBACKS}


def _apply_to_config(payload: dict[str, Any]) -> None:
    """Применяет рекомендуемые fallbacks в live конфиг."""
    agents = payload.setdefault("agents", {})
    defaults = agents.setdefault("defaults", {})
    model = defaults.setdefault("model", {})
    model["fallbacks"] = RECOMMENDED_FALLBACKS

    _RUNTIME_CONFIG_PATH.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\n✅ Конфиг обновлён: {_RUNTIME_CONFIG_PATH}")
    print("   Перезапусти OpenClaw Gateway чтобы применить: openclaw gateway")


def _copy_to_clipboard(text: str) -> bool:
    """Копирует текст в буфер обмена через pbcopy (macOS)."""
    try:
        subprocess.run(["pbcopy"], input=text.encode(), check=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


def main() -> None:
    args = sys.argv[1:]
    check_only = "--check" in args
    apply_mode = "--apply" in args
    auto_yes = "--yes" in args or "-y" in args

    payload = _read_config()
    current = _get_current_fallbacks(payload)
    agents = payload.get("agents") or {}
    defaults = (agents.get("defaults") or {}) if isinstance(agents, dict) else {}
    model_defaults = (defaults.get("model") or {}) if isinstance(defaults, dict) else {}
    primary = str(model_defaults.get("primary") or "").strip()

    # Anthropic-vertex модели в runtime
    av_models = _get_anthropic_vertex_models_in_runtime()

    print(f"Runtime config: {_RUNTIME_CONFIG_PATH}")
    print(f"Primary: {primary}")
    print(f"Anthropic-Vertex модели в models.json: {av_models or '(не найдены)'}\n")

    _check_chain(current)

    if check_only:
        return

    _print_recommended(primary)

    # JSON-фрагмент для ручного патча
    patch_json = json.dumps({"fallbacks": RECOMMENDED_FALLBACKS}, indent=2, ensure_ascii=False)

    if apply_mode:
        if auto_yes:
            confirm = "y"
            print("\n--yes: применяю автоматически.")
        else:
            try:
                confirm = input("\nПрименить изменения в runtime config? [y/N] ").strip().lower()
            except EOFError:
                confirm = "n"
                print("\n(non-interactive: пропускаю apply, используй --yes для autoapproval)")
        if confirm == "y":
            _apply_to_config(payload)
        else:
            print("Отменено.")
        return

    # Preview mode: показываем JSON-фрагмент
    print("\n=== JSON-патч для ручного применения ===")
    print("Путь: ~/.openclaw/openclaw.json → agents.defaults.model")
    print(patch_json)

    if _copy_to_clipboard(patch_json):
        print("\n📋 JSON скопирован в буфер обмена.")

    print("\nДля автоматического применения:")
    print(f"  python3 {sys.argv[0]} --apply")
    print("\nДля только проверки текущей цепочки:")
    print(f"  python3 {sys.argv[0]} --check")


if __name__ == "__main__":
    main()
