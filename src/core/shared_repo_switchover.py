# -*- coding: utf-8 -*-
"""
shared_repo_switchover.py — truth-модель переключения между `pablito` и shared repo.

Что это:
- небольшой аналитический слой, который честно сравнивает текущую рабочую копию
  и канонический shared repo;
- не делает destructive sync, а только показывает drift, overlap по файлам и
  безопасную стратегию переключения между macOS-учётками.

Зачем нужно:
- при разработке с нескольких учёток конфликт рождается не только из runtime,
  но и из двух разных git/worktree состояний;
- перед переходом на другую учётку нужно понимать, можно ли быстро продолжить
  работу из shared repo или сначала нужен ручной merge/patch review;
- этот слой должен быть пригоден и для `.command` helper, и для handoff bundle.
"""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
from pathlib import Path
from typing import Any


def _run_git(repo_root: Path, args: list[str], *, timeout_sec: float = 12.0) -> dict[str, Any]:
    """Запускает git-команду и возвращает structured payload без исключений наружу."""
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_sec,
        )
    except Exception as exc:  # noqa: BLE001 - helper должен быть fail-safe
        return {
            "ok": False,
            "returncode": 1,
            "stdout": "",
            "stderr": str(exc),
        }
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": (proc.stdout or "").strip(),
        "stderr": (proc.stderr or "").strip(),
    }


def parse_git_status_porcelain(text: str) -> list[dict[str, Any]]:
    """
    Парсит `git status --short` в список файловых изменений.

    Поддерживаем только нужный нам минимальный формат:
    - tracked status в первых двух символах;
    - untracked `??`;
    - rename `old -> new` нормализуем к новому пути.
    """
    items: list[dict[str, Any]] = []
    for raw_line in str(text or "").splitlines():
        line = str(raw_line or "").rstrip()
        if not line or line.startswith("##"):
            continue
        status = line[:2]
        raw_path = line[3:] if len(line) > 3 else ""
        path = raw_path.split(" -> ")[-1].strip()
        if not path:
            continue
        items.append(
            {
                "status": status,
                "path": path,
                "tracked": status != "??",
                "untracked": status == "??",
            }
        )
    return items


def parse_git_status_porcelain_z(text: str) -> list[dict[str, Any]]:
    """
    Парсит `git status --porcelain=v1 -z` в точные пути без shell-quoting.

    Почему нужен отдельный parser:
    - `git status --short` экранирует пробелы и Unicode-пути;
    - для multi-account overlap-анализа нам нужны реальные filesystem paths,
      иначе нельзя надёжно сравнить содержимое файлов между копиями.
    """
    items: list[dict[str, Any]] = []
    parts = [part for part in str(text or "").split("\0") if part]
    idx = 0
    while idx < len(parts):
        entry = parts[idx]
        if len(entry) < 3:
            idx += 1
            continue
        status = entry[:2]
        path = entry[3:] if len(entry) > 3 else ""
        if not path:
            idx += 1
            continue
        orig_path = ""
        if status[:1] in {"R", "C"} and (idx + 1) < len(parts):
            orig_path = parts[idx + 1]
            idx += 1
        items.append(
            {
                "status": status,
                "path": path,
                "orig_path": orig_path,
                "tracked": status != "??",
                "untracked": status == "??",
            }
        )
        idx += 1
    return items


def _write_access_probe(path: Path) -> dict[str, Any]:
    """Снимает факт существования и права записи текущей учётки для shared path."""
    exists = path.exists()
    writable = bool(os.access(path, os.W_OK)) if exists else False
    mode = ""
    if exists:
        try:
            mode = stat.filemode(path.stat().st_mode)
        except OSError:
            mode = ""
    return {
        "path": str(path),
        "exists": exists,
        "writable": writable,
        "mode": mode,
    }


def build_repo_snapshot(repo_root: Path) -> dict[str, Any]:
    """Собирает git/worktree snapshot для одного репозитория."""
    repo_root = Path(repo_root)
    git_dir = repo_root / ".git"
    exists = repo_root.exists()
    git_dir_exists = git_dir.exists()
    branch = (
        _run_git(repo_root, ["rev-parse", "--abbrev-ref", "HEAD"]).get("stdout", "")
        if git_dir_exists
        else ""
    )
    head = _run_git(repo_root, ["rev-parse", "HEAD"]).get("stdout", "") if git_dir_exists else ""
    status_short = (
        _run_git(repo_root, ["status", "--short", "--branch"]).get("stdout", "")
        if git_dir_exists
        else ""
    )
    status_z = (
        _run_git(repo_root, ["status", "--porcelain=v1", "-z"]).get("stdout", "")
        if git_dir_exists
        else ""
    )
    items = (
        parse_git_status_porcelain_z(status_z)
        if status_z
        else parse_git_status_porcelain(status_short)
    )
    dirty_paths = [item["path"] for item in items]
    tracked_dirty = [item["path"] for item in items if item.get("tracked")]
    untracked = [item["path"] for item in items if item.get("untracked")]
    return {
        "path": str(repo_root),
        "exists": exists,
        "git_dir_exists": git_dir_exists,
        "branch": str(branch or "").strip(),
        "head": str(head or "").strip(),
        "status_short": str(status_short or ""),
        "items": items,
        "dirty_count": len(dirty_paths),
        "tracked_dirty_count": len(tracked_dirty),
        "untracked_count": len(untracked),
        "dirty_paths": sorted(dirty_paths),
        "tracked_dirty_paths": sorted(tracked_dirty),
        "untracked_paths": sorted(untracked),
        "write_access": {
            "repo_root": _write_access_probe(repo_root),
            "docs_dir": _write_access_probe(repo_root / "docs"),
            "artifacts_dir": _write_access_probe(repo_root / "artifacts"),
        },
    }


def _sha256_bytes(raw: bytes) -> str:
    """Возвращает sha256 для файла; нужен для точного сравнения содержимого."""
    return hashlib.sha256(raw).hexdigest()


def _is_probably_text(raw: bytes) -> bool:
    """Грубая, но практичная эвристика текстового файла."""
    if b"\0" in raw:
        return False
    try:
        raw.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def _git_no_index_numstat(path_a: Path, path_b: Path) -> dict[str, Any]:
    """Снимает numstat между двумя путями без зависимости от git-index."""
    try:
        proc = subprocess.run(
            ["git", "diff", "--no-index", "--numstat", "--", str(path_a), str(path_b)],
            capture_output=True,
            text=True,
            check=False,
            timeout=12,
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "added": None, "deleted": None, "binary": False}
    line = (proc.stdout or "").strip().splitlines()
    if not line:
        return {"ok": True, "added": 0, "deleted": 0, "binary": False}
    cols = line[0].split("\t")
    if len(cols) < 3:
        return {
            "ok": False,
            "error": proc.stdout or proc.stderr or "",
            "added": None,
            "deleted": None,
            "binary": False,
        }
    if cols[0] == "-" or cols[1] == "-":
        return {"ok": True, "added": None, "deleted": None, "binary": True}
    try:
        return {"ok": True, "added": int(cols[0]), "deleted": int(cols[1]), "binary": False}
    except ValueError:
        return {
            "ok": False,
            "error": proc.stdout or proc.stderr or "",
            "added": None,
            "deleted": None,
            "binary": False,
        }


def analyze_overlap_paths(
    *,
    current_root: Path,
    shared_root: Path,
    overlap_paths: list[str],
) -> dict[str, Any]:
    """
    Разбирает overlap paths по фактическому содержимому файлов.

    Категории:
    - `identical` — git-state грязный в обеих копиях, но содержимое уже одинаковое;
    - `divergent_text` — обе копии изменили текстовый файл по-разному;
    - `divergent_binary` — обе копии изменили бинарный или нечитаемый файл;
    - `only_current` / `only_shared` — путь есть только в одной рабочей копии.
    """
    items: list[dict[str, Any]] = []
    counts = {
        "identical": 0,
        "divergent_text": 0,
        "divergent_binary": 0,
        "only_current": 0,
        "only_shared": 0,
    }

    for rel_path in sorted(set(str(path) for path in overlap_paths if str(path).strip())):
        current_path = Path(current_root) / rel_path
        shared_path = Path(shared_root) / rel_path
        current_exists = current_path.exists()
        shared_exists = shared_path.exists()

        item: dict[str, Any] = {
            "path": rel_path,
            "current_path": str(current_path),
            "shared_path": str(shared_path),
            "current_exists": current_exists,
            "shared_exists": shared_exists,
        }

        if current_exists and not shared_exists:
            item["category"] = "only_current"
            counts["only_current"] += 1
        elif shared_exists and not current_exists:
            item["category"] = "only_shared"
            counts["only_shared"] += 1
        elif not current_exists and not shared_exists:
            item["category"] = "missing_both"
        else:
            current_raw = current_path.read_bytes()
            shared_raw = shared_path.read_bytes()
            item["current_sha256"] = _sha256_bytes(current_raw)
            item["shared_sha256"] = _sha256_bytes(shared_raw)
            item["current_size_bytes"] = len(current_raw)
            item["shared_size_bytes"] = len(shared_raw)

            if item["current_sha256"] == item["shared_sha256"]:
                item["category"] = "identical"
                counts["identical"] += 1
            else:
                numstat = _git_no_index_numstat(current_path, shared_path)
                item["numstat"] = numstat
                if (
                    _is_probably_text(current_raw)
                    and _is_probably_text(shared_raw)
                    and not bool(numstat.get("binary"))
                ):
                    item["category"] = "divergent_text"
                    counts["divergent_text"] += 1
                else:
                    item["category"] = "divergent_binary"
                    counts["divergent_binary"] += 1

        items.append(item)

    return {
        "counts": counts,
        "items": items,
    }


def build_switchover_recommendation(
    *,
    current_repo: dict[str, Any],
    shared_repo: dict[str, Any],
) -> dict[str, Any]:
    """Возвращает безопасную стратегию переключения без авто-слияния worktree."""
    current_paths = set(current_repo.get("dirty_paths") or [])
    shared_paths = set(shared_repo.get("dirty_paths") or [])
    overlap_paths = sorted(current_paths & shared_paths)
    current_only_paths = sorted(current_paths - shared_paths)
    shared_only_paths = sorted(shared_paths - current_paths)
    branch_drift = str(current_repo.get("branch") or "") != str(shared_repo.get("branch") or "")
    head_drift = str(current_repo.get("head") or "") != str(shared_repo.get("head") or "")

    if not shared_repo.get("exists") or not shared_repo.get("git_dir_exists"):
        strategy_code = "shared_repo_missing"
        readiness = "blocked"
        summary = "Канонический shared repo отсутствует или не инициализирован как git-репозиторий."
    elif overlap_paths:
        strategy_code = "manual_merge_required"
        readiness = "attention"
        summary = "Обе копии изменяли одни и те же пути; нужен ручной merge или review patch перед переключением."
    elif current_repo.get("dirty_count") and not shared_repo.get("dirty_count"):
        strategy_code = "carry_pablito_wip_to_shared"
        readiness = "ready_with_patch"
        summary = "Shared repo чистый; текущий WIP можно переносить как patch или через осознанный commit/cherry-pick."
    elif shared_repo.get("dirty_count") and not current_repo.get("dirty_count"):
        strategy_code = "review_shared_wip_before_return"
        readiness = "attention"
        summary = "В shared repo уже есть локальный WIP; перед возвратом на pablito сначала нужно его разобрать."
    elif branch_drift or head_drift:
        strategy_code = "branch_or_head_drift"
        readiness = "attention"
        summary = "Ветки или HEAD расходятся; перед стартом на другой учётке сначала синхронизируй branch/HEAD осознанно."
    else:
        strategy_code = "shared_repo_ready"
        readiness = "ready"
        summary = "Shared repo синхронизирован с текущей копией и подходит как основная точка переключения."

    actions = [
        "Не запускать live runtime на второй учётке, пока не понятен текущий owner runtime и drift по repo.",
    ]
    if strategy_code == "carry_pablito_wip_to_shared":
        actions.extend(
            [
                "Сгенерировать patch текущего WIP и применять его в shared repo только после `git switch` на целевую ветку.",
                "После применения patch сразу прогнать локальные tests/docs checks на новой учётке.",
            ]
        )
    elif strategy_code == "manual_merge_required":
        actions.extend(
            [
                "Не пытаться автоматически копировать файлы между копиями.",
                "Сначала сравнить overlap paths и решить, где канонический вариант по каждому конфликтному файлу.",
            ]
        )
    elif strategy_code == "branch_or_head_drift":
        actions.extend(
            [
                "Сначала выровнять shared repo до нужной ветки или осознанно выбрать, что именно считается боевой рабочей копией.",
            ]
        )
    elif strategy_code == "review_shared_wip_before_return":
        actions.extend(
            [
                "Перед возвратом на pablito сохранить или зафиксировать WIP shared repo, чтобы не потерять чужую работу.",
            ]
        )

    return {
        "strategy_code": strategy_code,
        "readiness": readiness,
        "summary": summary,
        "branch_drift": branch_drift,
        "head_drift": head_drift,
        "overlap_count": len(overlap_paths),
        "current_only_count": len(current_only_paths),
        "shared_only_count": len(shared_only_paths),
        "overlap_paths_preview": overlap_paths[:20],
        "current_only_paths_preview": current_only_paths[:20],
        "shared_only_paths_preview": shared_only_paths[:20],
        "actions": actions,
    }


def build_shared_repo_switchover_report(
    *,
    current_root: Path,
    shared_root: Path,
) -> dict[str, Any]:
    """Собирает полный report для безопасного switchover между текущей и shared копией."""
    current_repo = build_repo_snapshot(current_root)
    shared_repo = build_repo_snapshot(shared_root)
    recommendation = build_switchover_recommendation(
        current_repo=current_repo,
        shared_repo=shared_repo,
    )
    overlap_analysis = analyze_overlap_paths(
        current_root=current_root,
        shared_root=shared_root,
        overlap_paths=list(recommendation.get("overlap_paths_preview") or []),
    )
    return {
        "ok": True,
        "mode": "shared_repo_switchover",
        "current_repo": current_repo,
        "shared_repo": shared_repo,
        "recommendation": recommendation,
        "overlap_analysis": overlap_analysis,
    }


__all__ = [
    "analyze_overlap_paths",
    "build_repo_snapshot",
    "build_shared_repo_switchover_report",
    "build_switchover_recommendation",
    "parse_git_status_porcelain",
    "parse_git_status_porcelain_z",
]
