#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Идемпотентно пере-применяет локальный hotfix OpenClaw для exec approvals.

Что чинит:
- в текущем OpenClaw per-call `exec.ask` может переэскалировать approval policy
  даже когда глобально выставлено `tools.exec.ask=off`;
- из-за этого Control UI снова показывает approval modal и ломает `Always allow`,
  хотя host/gateway truth уже приведён в порядок.

Что делает этот helper:
- находит установленный `pi-embedded-*.js` внутри Homebrew/NPM OpenClaw;
- проверяет, стоит ли уже наш hotfix;
- если нет, создаёт timestamped backup и патчит точку, где считается effective ask;
- после правки проверяет, что модуль всё ещё импортируется через Node.js.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence


DEFAULT_DIST_DIR = Path("/opt/homebrew/lib/node_modules/openclaw/dist")


@dataclass(frozen=True)
class PatchStep:
    """Описывает одну идемпотентную текстовую замену в dist-файле."""

    name: str
    old: str
    new: str
    marker: str


PATCH_STEPS: tuple[PatchStep, ...] = (
    PatchStep(
        name="clamp_exec_ask_when_global_off",
        old=(
            '\t\t\tconst configuredAsk = defaults?.ask ?? approvalDefaults?.ask ?? "off";\n'
            '\t\t\tlet ask = maxAsk(configuredAsk, normalizeExecAsk(params.ask) ?? configuredAsk);\n'
            '\t\t\tconst bypassApprovals = elevatedRequested && elevatedMode === "full";\n'
        ),
        new=(
            '\t\t\tconst configuredAsk = defaults?.ask ?? approvalDefaults?.ask ?? "off";\n'
            '\t\t\tconst requestedAsk = normalizeExecAsk(params.ask);\n'
            '\t\t\tlet ask = configuredAsk === "off" ? "off" : maxAsk(configuredAsk, requestedAsk ?? configuredAsk);\n'
            '\t\t\tconst bypassApprovals = elevatedRequested && elevatedMode === "full";\n'
        ),
        marker='const requestedAsk = normalizeExecAsk(params.ask);',
    ),
)


def _discover_target(dist_dir: Path) -> Path:
    """Находит единственный pi-embedded dist-файл текущей установки."""
    candidates = sorted(
        path for path in dist_dir.glob("pi-embedded-*.js")
        if not path.name.startswith("pi-embedded-helpers-")
    )
    if not candidates:
        raise FileNotFoundError(f"Не найден pi-embedded-*.js в {dist_dir}")
    if len(candidates) > 1:
        raise RuntimeError(
            "Найдено несколько pi-embedded dist-файлов, нужен ручной выбор: "
            + ", ".join(str(item) for item in candidates)
        )
    return candidates[0]


def _is_fully_patched(text: str) -> bool:
    """Проверяет, что все маркеры hotfix уже присутствуют."""
    return all(step.marker in text for step in PATCH_STEPS)


def _apply_patch_steps(text: str) -> tuple[str, list[str]]:
    """Применяет известные замены и возвращает новый текст плюс список шагов."""
    updated = text
    changed_steps: list[str] = []
    for step in PATCH_STEPS:
        if step.marker in updated:
            continue
        if step.old not in updated:
            raise RuntimeError(
                f"Не удалось применить шаг `{step.name}`: не найден ожидаемый upstream-фрагмент. "
                "Вероятно, layout dist-файла изменился."
            )
        updated = updated.replace(step.old, step.new, 1)
        changed_steps.append(step.name)
    return updated, changed_steps


def _timestamp_suffix() -> str:
    """Возвращает UTC-suffix для backup-файлов."""
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")


def _verify_node_import(target: Path) -> None:
    """Проверяет, что dist-модуль импортируется после правки."""
    cmd = [
        "node",
        "-e",
        (
            "import(process.argv[1])"
            ".then(() => console.log('import_ok'))"
            ".catch((err) => { console.error(err); process.exit(1); })"
        ),
        str(target),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def _build_result(
    *,
    ok: bool,
    target: Path | None,
    changed_steps: Sequence[str],
    backup_path: Path | None,
    already_patched: bool,
    error: str | None = None,
) -> dict[str, object]:
    """Собирает единый JSON-ответ для launcher-а и ручного запуска."""
    return {
        "ok": ok,
        "target": str(target) if target else None,
        "already_patched": already_patched,
        "changed_steps": list(changed_steps),
        "backup_path": str(backup_path) if backup_path else None,
        "restart_recommended": True,
        "error": error,
    }


def main() -> int:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(
        description="Идемпотентно пере-применяет локальный hotfix OpenClaw для exec ask=off."
    )
    parser.add_argument(
        "--dist-dir",
        type=Path,
        default=DEFAULT_DIST_DIR,
        help="Каталог dist установленного OpenClaw.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Только проверить состояние, без записи.",
    )
    args = parser.parse_args()

    target: Path | None = None
    backup_path: Path | None = None
    changed_steps: list[str] = []
    already_patched = False

    try:
        target = _discover_target(args.dist_dir)
        original = target.read_text(encoding="utf-8")
        already_patched = _is_fully_patched(original)

        if args.check:
            result = _build_result(
                ok=True,
                target=target,
                changed_steps=[],
                backup_path=None,
                already_patched=already_patched,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0

        if not already_patched:
            updated, changed_steps = _apply_patch_steps(original)
            backup_path = target.with_name(f"{target.name}.bak_exec_ask_off_{_timestamp_suffix()}")
            backup_path.write_text(original, encoding="utf-8")
            target.write_text(updated, encoding="utf-8")
            _verify_node_import(target)

        result = _build_result(
            ok=True,
            target=target,
            changed_steps=changed_steps,
            backup_path=backup_path,
            already_patched=already_patched,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:  # noqa: BLE001
        result = _build_result(
            ok=False,
            target=target,
            changed_steps=changed_steps,
            backup_path=backup_path,
            already_patched=already_patched,
            error=str(exc),
        )
        print(json.dumps(result, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
