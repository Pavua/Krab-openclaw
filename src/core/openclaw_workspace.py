# -*- coding: utf-8 -*-
"""
openclaw_workspace.py — доступ к каноническому workspace OpenClaw.

Что это:
- единая точка чтения persona/source-of-truth из `~/.openclaw/workspace-main-messaging`;
- helper для userbot, чтобы он не жил на отдельном hardcoded prompt;
- helper для общей текстовой памяти `!remember/!recall`, не плодящей второй
  независимый store рядом с runtime OpenClaw.

Зачем нужно:
- bot-контур OpenClaw уже опирается на hidden workspace;
- userbot раньше читал только локальный prompt и свою Chroma-память;
- из-за этого появлялась амнезия и расхождение поведения между каналами.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .logger import get_logger
from ..config import config


logger = get_logger(__name__)


WORKSPACE_PROMPT_FILES: tuple[str, ...] = ("SOUL.md", "USER.md", "TOOLS.md", "MEMORY.md")
_MEMORY_LINE_PATTERN = re.compile(
    r"^- (?P<time>\d{2}:\d{2}) \[(?P<source>[^\]:]+)(?::(?P<author>[^\]]+))?\] (?P<text>.+)$"
)


def resolve_main_workspace_dir(workspace_dir: Path | None = None) -> Path:
    """Возвращает канонический путь workspace-main-messaging."""
    candidate = workspace_dir or getattr(config, "OPENCLAW_MAIN_WORKSPACE_DIR", None)
    if isinstance(candidate, Path):
        return candidate.expanduser()
    return Path.home() / ".openclaw" / "workspace-main-messaging"


def _read_text(path: Path, *, max_chars: int, from_end: bool = False) -> str:
    if not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore").strip()
    except OSError as exc:
        logger.warning("openclaw_workspace_read_failed", path=str(path), error=str(exc))
        return ""
    if max_chars > 0 and len(text) > max_chars:
        if from_end:
            tail = text[-max_chars:]
            newline_idx = tail.find("\n")
            if newline_idx >= 0:
                tail = tail[newline_idx + 1 :]
            return "[...trimmed...]\n" + tail.strip()
        return text[:max_chars].rstrip() + "\n[...trimmed...]"
    return text


def load_workspace_prompt_bundle(
    *,
    workspace_dir: Path | None = None,
    max_chars_per_file: int = 1800,
    include_recent_memory_days: int = 2,
) -> str:
    """
    Собирает компактный prompt-bundle из канонического OpenClaw workspace.

    Для userbot достаточно persona + user prefs + tools + свежей памяти.
    Полный AGENTS bootstrap сюда намеренно не тащим, чтобы не раздувать prompt.
    """
    root = resolve_main_workspace_dir(workspace_dir)
    sections: list[str] = []

    for filename in WORKSPACE_PROMPT_FILES:
        content = _read_text(root / filename, max_chars=max_chars_per_file)
        if content:
            sections.append(f"[{filename}]\n{content}")

    memory_dir = root / "memory"
    if memory_dir.exists():
        today = datetime.now().date()
        for offset in range(max(0, int(include_recent_memory_days))):
            day = today - timedelta(days=offset)
            content = _read_text(
                memory_dir / f"{day.isoformat()}.md",
                max_chars=max_chars_per_file,
                from_end=True,
            )
            if content:
                sections.append(f"[memory/{day.isoformat()}.md]\n{content}")

    return "\n\n".join(section for section in sections if section).strip()


def append_workspace_memory_entry(
    text: str,
    *,
    workspace_dir: Path | None = None,
    source: str = "userbot",
    author: str = "",
) -> bool:
    """Добавляет запись в дневной markdown-файл общей памяти OpenClaw."""
    normalized = str(text or "").strip()
    if not normalized:
        return False

    root = resolve_main_workspace_dir(workspace_dir)
    memory_dir = root / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now()
    day_path = memory_dir / f"{today.date().isoformat()}.md"

    author_suffix = f":{author.strip()}" if str(author or "").strip() else ""
    line = f"- {today.strftime('%H:%M')} [{str(source or 'userbot').strip()}{author_suffix}] {normalized}"
    try:
        if day_path.exists():
            existing = day_path.read_text(encoding="utf-8", errors="ignore").rstrip()
            prefix = existing + ("\n" if existing else "")
            day_path.write_text(prefix + line + "\n", encoding="utf-8")
        else:
            header = f"# Memory {today.date().isoformat()}\n\n"
            day_path.write_text(header + line + "\n", encoding="utf-8")
        return True
    except OSError as exc:
        logger.warning("openclaw_workspace_memory_append_failed", path=str(day_path), error=str(exc))
        return False


def _query_tokens(query: str) -> list[str]:
    tokens = re.findall(r"[0-9A-Za-zА-Яа-яЁё_-]{3,}", str(query or "").lower())
    return list(dict.fromkeys(tokens))


def recall_workspace_memory(
    query: str,
    *,
    workspace_dir: Path | None = None,
    max_results: int = 5,
    max_chars: int = 1600,
) -> str:
    """
    Ищет текстовые совпадения в канонической памяти OpenClaw.

    Это не векторный поиск, а надёжный fallback по markdown-памяти workspace:
    он нужен, чтобы userbot видел ту же дневную память, что и runtime OpenClaw.
    """
    tokens = _query_tokens(query)
    if not tokens:
        return ""

    root = resolve_main_workspace_dir(workspace_dir)
    candidate_files: list[Path] = []
    memory_md = root / "MEMORY.md"
    if memory_md.exists():
        candidate_files.append(memory_md)
    memory_dir = root / "memory"
    if memory_dir.exists():
        candidate_files.extend(sorted(memory_dir.glob("*.md"), reverse=True))

    matches: list[tuple[int, str, str]] = []
    for path in candidate_files:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError as exc:
            logger.warning("openclaw_workspace_memory_read_failed", path=str(path), error=str(exc))
            continue
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            lowered = line.lower()
            score = sum(1 for token in tokens if token in lowered)
            if score <= 0:
                continue
            matches.append((score, path.name, line))

    matches.sort(key=lambda item: (-item[0], item[1], item[2]))
    rendered: list[str] = []
    for _, filename, line in matches[: max(1, int(max_results))]:
        rendered.append(f"- [{filename}] {line}")

    result = "\n".join(rendered).strip()
    if max_chars > 0 and len(result) > max_chars:
        return result[:max_chars].rstrip() + "\n[...trimmed...]"
    return result


def list_workspace_memory_entries(
    *,
    workspace_dir: Path | None = None,
    limit: int = 10,
    source_filter: str = "",
) -> list[dict[str, str]]:
    """
    Возвращает последние записи из общей markdown-памяти OpenClaw.

    Зачем это нужно:
    - `!recall` хорош для поиска по словам, но не показывает просто "что было недавно";
    - proactive watch пишет короткие operational digest в ту же память;
    - владельцу нужен быстрый просмотр последних записей без ручного чтения md-файлов.
    """
    root = resolve_main_workspace_dir(workspace_dir)
    memory_dir = root / "memory"
    if not memory_dir.exists():
        return []

    normalized_source = str(source_filter or "").strip().lower()
    results: list[dict[str, str]] = []
    for path in sorted(memory_dir.glob("*.md"), reverse=True):
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError as exc:
            logger.warning("openclaw_workspace_memory_read_failed", path=str(path), error=str(exc))
            continue
        for raw_line in reversed(lines):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            match = _MEMORY_LINE_PATTERN.match(line)
            if not match:
                continue
            source = str(match.group("source") or "").strip()
            if normalized_source and normalized_source not in source.lower():
                continue
            results.append(
                {
                    "date": path.stem,
                    "time": str(match.group("time") or "").strip(),
                    "source": source,
                    "author": str(match.group("author") or "").strip(),
                    "text": str(match.group("text") or "").strip(),
                }
            )
            if len(results) >= max(1, int(limit)):
                return results
    return results


def build_workspace_state_snapshot(
    *,
    workspace_dir: Path | None = None,
    recent_entries_limit: int = 3,
) -> dict[str, Any]:
    """
    Возвращает machine-readable snapshot общего OpenClaw workspace/state.

    Зачем это нужно:
    - shared workspace должен быть виден как отдельная runtime-сущность, а не только
      как декларация `shared_memory=true` в capability registry;
    - owner UI, handoff bundle и reserve smoke должны видеть один и тот же truthful срез;
    - это даёт минимальный, но реальный мост между userbot и reserve transport без
      дублирования второй памяти или второго workspace.
    """
    root = resolve_main_workspace_dir(workspace_dir)
    memory_dir = root / "memory"

    prompt_files: dict[str, dict[str, Any]] = {}
    prompt_files_present: list[str] = []
    for filename in WORKSPACE_PROMPT_FILES:
        path = root / filename
        exists = path.exists()
        prompt_files[filename] = {
            "path": str(path),
            "exists": exists,
        }
        if exists:
            prompt_files_present.append(filename)

    memory_file_count = 0
    if memory_dir.exists():
        memory_file_count = sum(1 for _ in memory_dir.glob("*.md"))

    recent_entries = list_workspace_memory_entries(
        workspace_dir=root,
        limit=max(1, int(recent_entries_limit)),
    )

    return {
        "ok": True,
        "workspace_dir": str(root),
        "exists": root.exists(),
        "shared_workspace_attached": root.exists() and bool(prompt_files_present),
        "shared_memory_ready": root.exists(),
        "memory_dir": str(memory_dir),
        "memory_dir_exists": memory_dir.exists(),
        "prompt_files": prompt_files,
        "prompt_files_present": prompt_files_present,
        "prompt_files_present_count": len(prompt_files_present),
        "memory_file_count": memory_file_count,
        "recent_memory_entries_count": len(recent_entries),
        "recent_memory_entries": recent_entries,
        "last_memory_entry": dict(recent_entries[0]) if recent_entries else {},
    }
