#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Идемпотентно пере-применяет локальный patch для OpenClaw session-utils.

Что делает:
- находит установленный `session-utils-*.js` внутри Homebrew/NPM OpenClaw;
- проверяет, стоит ли уже наш live-патч против stale sessions;
- если патча нет, создаёт timestamped backup и применяет точечные замены;
- после правки проверяет, что модуль всё ещё импортируется через Node.js.

Зачем нужен:
- после `npm install -g openclaw`, `brew upgrade` или другой переустановки
  OpenClaw локальный `dist`-патч исчезает;
- этот helper возвращает рабочее поведение `:18789/sessions` одним запуском,
  без ручного редактирования minified/dist-файла.
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
    """Описывает одну идемпотентную текстовую замену."""

    name: str
    old: str
    new: str
    marker: str


PATCH_STEPS: tuple[PatchStep, ...] = (
    PatchStep(
        name="readSessionMessages",
        old=(
            'function readSessionMessages(sessionId, storePath, sessionFile) {\n'
            '\tconst filePath = resolveSessionTranscriptCandidates(sessionId, storePath, sessionFile).find((p) => fsSync.existsSync(p));\n'
        ),
        new=(
            'function readSessionMessages(sessionId, storePath, sessionFile) {\n'
            '\tconst filePath = findExistingTranscriptPath(sessionId, storePath, sessionFile);\n'
        ),
        marker='const filePath = findExistingTranscriptPath(sessionId, storePath, sessionFile);',
    ),
    PatchStep(
        name="readSessionTitleFieldsFromTranscript",
        old=(
            'function readSessionTitleFieldsFromTranscript(sessionId, storePath, sessionFile, agentId, opts) {\n'
            '\tconst filePath = resolveSessionTranscriptCandidates(sessionId, storePath, sessionFile, agentId).find((p) => fsSync.existsSync(p));\n'
        ),
        new=(
            'function readSessionTitleFieldsFromTranscript(sessionId, storePath, sessionFile, agentId, opts) {\n'
            '\tconst filePath = findExistingTranscriptPath(sessionId, storePath, sessionFile, agentId);\n'
        ),
        marker='const filePath = findExistingTranscriptPath(sessionId, storePath, sessionFile, agentId);',
    ),
    PatchStep(
        name="findExistingTranscriptPath",
        old=(
            'function findExistingTranscriptPath(sessionId, storePath, sessionFile, agentId) {\n'
            '\treturn resolveSessionTranscriptCandidates(sessionId, storePath, sessionFile, agentId).find((p) => fsSync.existsSync(p)) ?? null;\n'
            '}\n'
        ),
        new=(
            'function findExistingTranscriptPath(sessionId, storePath, sessionFile, agentId) {\n'
            '\tconst candidate = resolveSessionTranscriptCandidates(sessionId, storePath, sessionFile, agentId).find((p) => fsSync.existsSync(p)) ?? null;\n'
            '\tif (candidate) return candidate;\n'
            '\tconst normalizedSessionId = typeof sessionId === "string" ? sessionId.trim() : "";\n'
            '\tif (!normalizedSessionId) return null;\n'
            '\ttry {\n'
            '\t\tconst fallbackDir = sessionFile ? path.dirname(sessionFile) : storePath;\n'
            '\t\tconst baseFileName = `${normalizedSessionId}.jsonl`;\n'
            '\t\tconst entries = fsSync.readdirSync(fallbackDir, {\n'
            '\t\t\twithFileTypes: true\n'
            '\t\t}).filter((entry) => entry.isFile() && (entry.name === baseFileName || entry.name.startsWith(`${baseFileName}.reset.`) || entry.name.startsWith(`${baseFileName}.deleted.`))).map((entry) => entry.name).toSorted((a, b) => b.localeCompare(a));\n'
            '\t\tconst fallbackName = entries[0];\n'
            '\t\treturn fallbackName ? path.join(fallbackDir, fallbackName) : null;\n'
            '\t} catch {\n'
            '\t\treturn null;\n'
            '\t}\n'
            '}\n'
        ),
        marker='entry.name.startsWith(`${baseFileName}.deleted.`)',
    ),
    PatchStep(
        name="readSessionPreviewItemsFromTranscript",
        old=(
            'function readSessionPreviewItemsFromTranscript(sessionId, storePath, sessionFile, agentId, maxItems, maxChars) {\n'
            '\tconst filePath = resolveSessionTranscriptCandidates(sessionId, storePath, sessionFile, agentId).find((p) => fsSync.existsSync(p));\n'
        ),
        new=(
            'function readSessionPreviewItemsFromTranscript(sessionId, storePath, sessionFile, agentId, maxItems, maxChars) {\n'
            '\tconst filePath = findExistingTranscriptPath(sessionId, storePath, sessionFile, agentId);\n'
        ),
        marker='const filePath = findExistingTranscriptPath(sessionId, storePath, sessionFile, agentId);',
    ),
    PatchStep(
        name="listSessionsFromStore",
        old=(
            '\t}).filter(([, entry]) => {\n'
            '\t\tif (!label) return true;\n'
            '\t\treturn entry?.label === label;\n'
            '\t}).map(([key, entry]) => buildGatewaySessionRow({\n'
        ),
        new=(
            '\t}).filter(([, entry]) => {\n'
            '\t\tif (!label) return true;\n'
            '\t\treturn entry?.label === label;\n'
            '\t}).filter(([key, entry]) => {\n'
            '\t\tconst sessionId = typeof entry?.sessionId === "string" ? entry.sessionId.trim() : "";\n'
            '\t\tconst sessionFile = typeof entry?.sessionFile === "string" ? entry.sessionFile.trim() : "";\n'
            '\t\tif (!sessionId || !sessionFile) return true;\n'
            '\t\tconst sessionAgentId = normalizeAgentId(parseAgentSessionKey(key)?.agentId ?? resolveDefaultAgentId(cfg));\n'
            '\t\treturn findExistingTranscriptPath(sessionId, storePath, sessionFile, sessionAgentId) !== null;\n'
            '\t}).map(([key, entry]) => buildGatewaySessionRow({\n'
        ),
        marker='return findExistingTranscriptPath(sessionId, storePath, sessionFile, sessionAgentId) !== null;',
    ),
)


def _discover_target(dist_dir: Path) -> Path:
    """Находит единственный session-utils dist-файл текущей установки."""
    candidates = sorted(dist_dir.glob("session-utils-*.js"))
    if not candidates:
        raise FileNotFoundError(f"Не найден session-utils-*.js в {dist_dir}")
    if len(candidates) > 1:
        raise RuntimeError(
            "Найдено несколько session-utils dist-файлов, нужен ручной выбор: "
            + ", ".join(str(item) for item in candidates)
        )
    return candidates[0]


def _is_fully_patched(text: str) -> bool:
    """Проверяет, что все ключевые маркеры уже присутствуют."""
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
        description="Идемпотентно пере-применяет локальный patch OpenClaw session-utils."
    )
    parser.add_argument(
        "--dist-dir",
        type=Path,
        default=DEFAULT_DIST_DIR,
        help="Каталог dist установленного OpenClaw.",
    )
    parser.add_argument(
        "--target",
        type=Path,
        help="Явный путь к session-utils-*.js. Если не указан, файл будет найден автоматически.",
    )
    args = parser.parse_args()

    try:
        target = args.target if args.target else _discover_target(args.dist_dir)
        original_text = target.read_text(encoding="utf-8")
        if _is_fully_patched(original_text):
            _verify_node_import(target)
            print(
                json.dumps(
                    _build_result(
                        ok=True,
                        target=target,
                        changed_steps=[],
                        backup_path=None,
                        already_patched=True,
                    ),
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0

        updated_text, changed_steps = _apply_patch_steps(original_text)
        backup_path = target.with_name(f"{target.name}.bak_{_timestamp_suffix()}")
        backup_path.write_text(original_text, encoding="utf-8")
        target.write_text(updated_text, encoding="utf-8")
        _verify_node_import(target)
        print(
            json.dumps(
                _build_result(
                    ok=True,
                    target=target,
                    changed_steps=changed_steps,
                    backup_path=backup_path,
                    already_patched=False,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except Exception as exc:  # noqa: BLE001 - нужен честный CLI-выход без traceback-шума.
        print(
            json.dumps(
                _build_result(
                    ok=False,
                    target=args.target if args.target else None,
                    changed_steps=[],
                    backup_path=None,
                    already_patched=False,
                    error=str(exc),
                ),
                ensure_ascii=False,
                indent=2,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
