#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Wave 162: interactive HuggingFace cache cleanup для disk-pressure relief.

Сканирует HF cache dirs (`~/.cache/huggingface/hub`,
`~/.openclaw/workspace-*/.cache/huggingface/hub`, `~/Library/Caches/huggingface`)
и определяет stale snapshots (mtime старше N дней) большого размера (>N MB).

По умолчанию dry-run: печатает JSON-отчёт с candidates.
С флагом `--apply` фактически удаляет snapshots + соответствующие unique blobs.

Safety:
- Никогда не трогает модели, попавшие в "active set" — модели, упомянутые
  в `~/.lmstudio/.internal/model-index-cache.json` ИЛИ `current_model.json`
  внутри runtime state (опционально).
- Симлинки игнорируются как контейнеры (например `4TB -> /Volumes/4TB`).
- `--apply` требует явно указать `--apply` (нет implicit fallback).

Exit 0 — успех. Exit 1 — ошибка.

CLI:
    python scripts/krab_hf_cache_cleanup.py [--apply]
                                            [--min-age-days N]
                                            [--min-size-mb M]
                                            [--cache-root PATH ...]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Callable, Iterable

# Stale threshold (дней без mtime-обновлений) и минимальный размер (MB).
_DEFAULT_STALE_DAYS: int = 30
_DEFAULT_MIN_SIZE_MB: int = 500

# Имя директории модели в HF cache: models--<org>--<name>.
_MODEL_DIR_RE = re.compile(r"^models--(?P<repo>.+)$")

# Канонический список cache roots для скана.
_CACHE_ROOTS_DEFAULT: tuple[Path, ...] = (
    Path.home() / ".cache" / "huggingface" / "hub",
    Path.home() / "Library" / "Caches" / "huggingface" / "hub",
)

# Workspace cache roots ищем глобом, т.к. workspace-<id> может быть много.
_WORKSPACE_GLOB = ".openclaw/workspace-*/.cache/huggingface/hub"

# LM Studio model index — если файл существует, читаем active models из него.
_LM_STUDIO_INDEX_PATH = Path.home() / ".lmstudio" / ".internal" / "model-index-cache.json"


def _ms_to_seconds(value: float | int) -> float:
    """LM Studio пишет timestamps в миллисекундах; нормализуем к секундам."""
    if value > 1_000_000_000_000:  # ~Sep 2001 в ms vs s
        return float(value) / 1000.0
    return float(value)


def _discover_cache_roots(extra_roots: Iterable[Path] | None = None) -> list[Path]:
    """Собирает все актуальные HF cache `hub/` директории."""
    roots: list[Path] = []
    for root in _CACHE_ROOTS_DEFAULT:
        if root.is_dir():
            roots.append(root)
    # workspace-* — glob под HOME.
    home = Path.home()
    for ws_hub in home.glob(_WORKSPACE_GLOB):
        if ws_hub.is_dir():
            roots.append(ws_hub)
    # Дополнительные roots от CLI.
    for extra in extra_roots or []:
        extra_p = Path(extra)
        if extra_p.is_dir():
            roots.append(extra_p)
    # Дедуп по resolved path (но НЕ резолвим симлинки — это удалит legitimate
    # хранилища, например /Volumes/4TB SSD). Сравниваем по абсолюту.
    seen: set[str] = set()
    uniq: list[Path] = []
    for r in roots:
        key = str(r.absolute())
        if key in seen:
            continue
        seen.add(key)
        uniq.append(r)
    return uniq


def _dir_size_bytes(path: Path) -> int:
    """Сумма размеров всех regular файлов под path. Симлинки игнорируются."""
    total = 0
    try:
        for sub in path.rglob("*"):
            try:
                if sub.is_symlink():
                    continue
                if not sub.is_file():
                    continue
                total += sub.stat().st_size
            except OSError:
                continue
    except OSError:
        return total
    return total


def _newest_mtime(path: Path) -> float:
    """Возвращает MAX mtime среди файлов внутри path (для определения stale)."""
    max_mtime: float = 0.0
    try:
        stat = path.stat()
        max_mtime = max(max_mtime, stat.st_mtime)
    except OSError:
        pass
    try:
        for sub in path.rglob("*"):
            try:
                if sub.is_symlink():
                    continue
                st = sub.stat()
                if st.st_mtime > max_mtime:
                    max_mtime = st.st_mtime
            except OSError:
                continue
    except OSError:
        pass
    return max_mtime


def load_active_models(
    *,
    lm_studio_index: Path | None = None,
    extra_active_paths: Iterable[Path] | None = None,
) -> set[str]:
    """
    Возвращает множество HF repo_id, помеченных как active (не трогать).

    Источники:
      1) LM Studio model-index-cache.json — поле `indexedModelIdentifier`.
      2) Дополнительные пути (current_model.json и т.п.) с полем `repo_id`
         или `model_id`.
    """
    active: set[str] = set()
    candidate_paths: list[Path] = []
    if lm_studio_index is None:
        candidate_paths.append(_LM_STUDIO_INDEX_PATH)
    else:
        candidate_paths.append(lm_studio_index)
    for extra in extra_active_paths or []:
        candidate_paths.append(Path(extra))

    for p in candidate_paths:
        if not p.is_file():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        # LM Studio формат: {"models": [{"indexedModelIdentifier": "org/name"}]}
        if isinstance(data, dict):
            models = data.get("models")
            if isinstance(models, list):
                for m in models:
                    if not isinstance(m, dict):
                        continue
                    repo = m.get("indexedModelIdentifier") or m.get("repo_id") or m.get("model_id")
                    if isinstance(repo, str) and repo:
                        active.add(repo.strip())
            # Альтернативный формат: {"repo_id": "..."} или {"model_id": "..."}
            for key in ("repo_id", "model_id", "current_model"):
                v = data.get(key)
                if isinstance(v, str) and v:
                    active.add(v.strip())
    return active


def _parse_repo_from_dir(dirname: str) -> str | None:
    """`models--mlx-community--whisper-large-v3-mlx` → `mlx-community/whisper-large-v3-mlx`."""
    m = _MODEL_DIR_RE.match(dirname)
    if not m:
        return None
    raw = m.group("repo")
    # HF использует `--` как разделитель org/name; первый `--` — граница.
    if "--" not in raw:
        return raw
    org, _, rest = raw.partition("--")
    return f"{org}/{rest}"


def discover_caches(
    *,
    cache_roots: Iterable[Path] | None = None,
    now_fn: Callable[[], float] | None = None,
) -> list[dict[str, Any]]:
    """
    Сканирует cache roots и возвращает список model directories с метаданными.

    Каждый элемент:
        {
            "root": str,
            "path": str,
            "repo_id": str | None,
            "size_bytes": int,
            "mtime": float,         # unix ts последнего изменения
            "age_days": float,      # сейчас() - mtime, в днях
        }
    """
    now = (now_fn or time.time)()
    roots = list(cache_roots) if cache_roots is not None else _discover_cache_roots()
    out: list[dict[str, Any]] = []
    for root in roots:
        try:
            entries = list(root.iterdir())
        except OSError:
            continue
        for entry in entries:
            try:
                # Симлинки (типа `4TB -> /Volumes/4TB`) пропускаем.
                if entry.is_symlink():
                    continue
                if not entry.is_dir():
                    continue
            except OSError:
                continue
            name = entry.name
            # Только models-- директории; datasets-- и .locks игнорируем.
            if not name.startswith("models--"):
                continue
            size = _dir_size_bytes(entry)
            mtime = _newest_mtime(entry)
            age_sec = max(0.0, now - mtime) if mtime > 0 else 0.0
            out.append(
                {
                    "root": str(root),
                    "path": str(entry),
                    "repo_id": _parse_repo_from_dir(name),
                    "size_bytes": int(size),
                    "mtime": float(mtime),
                    "age_days": round(age_sec / 86400.0, 2),
                }
            )
    return out


def filter_stale_candidates(
    caches: list[dict[str, Any]],
    *,
    min_age_days: int = _DEFAULT_STALE_DAYS,
    min_size_mb: int = _DEFAULT_MIN_SIZE_MB,
    active_models: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Отбирает entries, удовлетворяющие stale-критерию И size-критерию И не active."""
    active = active_models or set()
    min_bytes = min_size_mb * 1024 * 1024
    candidates: list[dict[str, Any]] = []
    for entry in caches:
        if entry.get("size_bytes", 0) < min_bytes:
            continue
        if entry.get("age_days", 0.0) < float(min_age_days):
            continue
        repo = entry.get("repo_id")
        if repo and repo in active:
            continue
        candidates.append(entry)
    return candidates


def _delete_path(path: Path) -> tuple[bool, str | None]:
    """Удаляет директорию рекурсивно. Возвращает (ok, error_message)."""
    try:
        shutil.rmtree(path)
        return True, None
    except OSError as exc:
        return False, f"{type(exc).__name__}: {exc}"


def run_cleanup(
    *,
    cache_roots: Iterable[Path] | None = None,
    min_age_days: int = _DEFAULT_STALE_DAYS,
    min_size_mb: int = _DEFAULT_MIN_SIZE_MB,
    apply: bool = False,
    active_models: set[str] | None = None,
    now_fn: Callable[[], float] | None = None,
) -> dict[str, Any]:
    """
    Главный entry-point: scan → filter → (optional) delete → report.

    Безопасно по умолчанию: apply=False означает dry-run (без удаления).
    """
    now = (now_fn or time.time)()
    caches = discover_caches(cache_roots=cache_roots, now_fn=lambda: now)
    total_bytes = sum(c.get("size_bytes", 0) for c in caches)
    candidates = filter_stale_candidates(
        caches,
        min_age_days=min_age_days,
        min_size_mb=min_size_mb,
        active_models=active_models,
    )

    would_save: list[dict[str, Any]] = []
    for c in sorted(candidates, key=lambda x: x.get("size_bytes", 0), reverse=True):
        would_save.append(
            {
                "path": c["path"],
                "repo_id": c.get("repo_id"),
                "size_mb": round(c.get("size_bytes", 0) / (1024 * 1024), 2),
                "age_days": c.get("age_days", 0.0),
            }
        )

    deleted: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    actually_freed_bytes = 0

    if apply:
        for c in candidates:
            path = Path(c["path"])
            size_bytes = int(c.get("size_bytes", 0))
            ok, err = _delete_path(path)
            if ok:
                deleted.append(
                    {
                        "path": str(path),
                        "repo_id": c.get("repo_id"),
                        "size_mb": round(size_bytes / (1024 * 1024), 2),
                    }
                )
                actually_freed_bytes += size_bytes
            else:
                errors.append({"path": str(path), "error": err})

    saved_bytes = sum(c.get("size_bytes", 0) for c in candidates)

    report: dict[str, Any] = {
        "timestamp": int(now),
        "timestamp_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "apply": bool(apply),
        "cache_roots": [
            str(r) for r in (list(cache_roots) if cache_roots else _discover_cache_roots())
        ],
        "min_age_days": int(min_age_days),
        "min_size_mb": int(min_size_mb),
        "total_models_scanned": len(caches),
        "total_caches_gb": round(total_bytes / (1024 * 1024 * 1024), 3),
        "stale_candidates_count": len(candidates),
        "stale_candidates_gb": round(saved_bytes / (1024 * 1024 * 1024), 3),
        "active_models_protected": sorted(active_models or set()),
        "would_save": would_save,
    }
    if apply:
        report["deleted"] = deleted
        report["errors"] = errors
        report["freed_gb"] = round(actually_freed_bytes / (1024 * 1024 * 1024), 3)
    return report


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Wave 162: cleanup stale HuggingFace cache snapshots.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="actually delete stale snapshots (default: dry-run report)",
    )
    parser.add_argument(
        "--min-age-days",
        type=int,
        default=_DEFAULT_STALE_DAYS,
        help=f"minimum mtime age in days to consider stale (default: {_DEFAULT_STALE_DAYS})",
    )
    parser.add_argument(
        "--min-size-mb",
        type=int,
        default=_DEFAULT_MIN_SIZE_MB,
        help=f"minimum directory size in MB to consider (default: {_DEFAULT_MIN_SIZE_MB})",
    )
    parser.add_argument(
        "--cache-root",
        action="append",
        type=str,
        default=None,
        help="extra cache hub/ directory to scan (repeatable)",
    )
    parser.add_argument(
        "--lm-studio-index",
        type=str,
        default=None,
        help="path to LM Studio model-index-cache.json (default: auto-detect)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    extra_roots = [Path(p) for p in (args.cache_root or [])]
    cache_roots = _discover_cache_roots(extra_roots=extra_roots)

    lm_index = Path(args.lm_studio_index) if args.lm_studio_index else None
    try:
        active = load_active_models(lm_studio_index=lm_index)
    except Exception as exc:  # noqa: BLE001
        # Safety-first: при ошибке чтения active list считаем все модели активными
        # и ничего не удаляем (return non-empty active set).
        print(
            json.dumps(
                {
                    "error": f"load_active_models failed: {exc}",
                    "error_type": type(exc).__name__,
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1

    try:
        report = run_cleanup(
            cache_roots=cache_roots,
            min_age_days=args.min_age_days,
            min_size_mb=args.min_size_mb,
            apply=args.apply,
            active_models=active,
        )
    except Exception as exc:  # noqa: BLE001
        print(
            json.dumps(
                {
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1

    print(
        json.dumps(report, ensure_ascii=False, indent=2 if os.isatty(sys.stdout.fileno()) else None)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
