# -*- coding: utf-8 -*-
"""
Сборщик anti-413 transition-пакета для быстрого старта нового диалога.

Зачем:
1. Снизить риск потери контекста при ошибке 413;
2. Собирать единый минимальный набор файлов без ручной рутины;
3. Дать владельцу проекта готовый пакет "прикрепи и продолжай".

Связь с проектом:
- использует `prepare_next_chat_context.command` и `scripts/new_chat_checkpoint.py`;
- пишет артефакты в `artifacts/context_transition/pack_<timestamp>`.
"""

from __future__ import annotations

import datetime as dt
import pathlib
import shutil
import subprocess
import sys
from typing import Iterable, List


ROOT = pathlib.Path(__file__).resolve().parents[1]
TRANSITION_ROOT = ROOT / "artifacts" / "context_transition"


def _run(cmd: List[str]) -> str:
    """Запускает команду и возвращает stdout (или stderr, если stdout пуст)."""
    proc = subprocess.run(
        cmd,
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    payload = (proc.stdout or "").strip()
    if payload:
        return payload
    return (proc.stderr or "").strip()


def _latest_file(pattern: str) -> pathlib.Path | None:
    """Возвращает самый свежий файл по glob-паттерну."""
    files = sorted(ROOT.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def _copy_if_exists(src: pathlib.Path, dest_dir: pathlib.Path) -> pathlib.Path | None:
    """Копирует файл в целевую папку, если источник существует."""
    if not src.exists() or not src.is_file():
        return None
    target = dest_dir / src.name
    shutil.copy2(src, target)
    return target


def _lines(items: Iterable[pathlib.Path]) -> str:
    return "\n".join(f"- {p.name}" for p in items)


def main() -> int:
    TRANSITION_ROOT.mkdir(parents=True, exist_ok=True)
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    pack_dir = TRANSITION_ROOT / f"pack_{ts}"
    pack_dir.mkdir(parents=True, exist_ok=True)

    # 1) Актуализируем чекпоинты.
    _run(["./prepare_next_chat_context.command"])
    _run([sys.executable, "scripts/new_chat_checkpoint.py"])

    # 2) Выбираем базовые файлы.
    latest_context = _latest_file("artifacts/context/next_chat_context_*.md")
    latest_checkpoint = _latest_file("artifacts/context_checkpoints/checkpoint_*.md")

    mandatory = [
        ROOT / "AGENTS.md",
        ROOT / "HANDOVER.md",
        ROOT / "ROADMAP.md",
        ROOT / "docs" / "CHAT_TRANSITION_PLAYBOOK_RU.md",
    ]

    copied: List[pathlib.Path] = []
    for file_path in mandatory:
        copied_file = _copy_if_exists(file_path, pack_dir)
        if copied_file:
            copied.append(copied_file)

    for optional in [latest_context, latest_checkpoint]:
        if optional:
            copied_file = _copy_if_exists(optional, pack_dir)
            if copied_file:
                copied.append(copied_file)

    # 3) Готовим компактный промпт-передачу.
    branch = _run(["git", "branch", "--show-current"]) or "unknown"
    head = _run(["git", "rev-parse", "--short", "HEAD"]) or "unknown"
    dirty = _run(["git", "status", "--short"])
    dirty_count = len([ln for ln in dirty.splitlines() if ln.strip()])

    transfer_prompt = pack_dir / "TRANSFER_PROMPT_RU.md"
    transfer_prompt.write_text(
        "\n".join(
            [
                "# Стартовый промпт для нового диалога (anti-413)",
                "",
                "Скопируй блок ниже в самое первое сообщение нового диалога:",
                "",
                "```text",
                "[CHECKPOINT]",
                f"branch={branch}",
                f"head={head}",
                f"changed_files={dirty_count}",
                "focus=продолжить разработку без повторов, принять свежие внешние поставки",
                "done=ключевые R-этапы уже интегрированы и частично протестированы",
                "next=1) проверить API/UI контракт 2) прогнать targeted pytest 3) коммит+push",
                "risks=шумные изменения из параллельных окон, payload-limit 413",
                "```",
                "",
                "После этого прикрепи все файлы из текущего pack.",
            ]
        ),
        encoding="utf-8",
    )

    files_list = pack_dir / "FILES_TO_ATTACH.txt"
    files_list.write_text(
        "\n".join(
            [
                "Прикрепить в новый диалог (в порядке):",
                _lines(sorted(pack_dir.glob("*.md"))),
                "",
                "Минимальный набор (если нужно совсем коротко):",
                "- TRANSFER_PROMPT_RU.md",
                "- next_chat_context_*.md (самый свежий)",
                "- checkpoint_*.md (самый свежий)",
            ]
        ),
        encoding="utf-8",
    )

    print(f"✅ Transition pack собран: {pack_dir}")
    print(f"📎 Файлов внутри: {len(list(pack_dir.glob('*')))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
