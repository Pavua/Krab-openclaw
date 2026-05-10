#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Wave 56-I-audit-rotation: CLI-помощник для ротации audit-логов.

Usage:
    python scripts/krab_audit_rotate.py --check   # показать размеры + need-rotate flag
    python scripts/krab_audit_rotate.py --force    # ротировать прямо сейчас
    python scripts/krab_audit_rotate.py            # то же что --check

JSON output на stdout, errors на stderr.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

# Перенаправить все логи на stderr ДО импорта src-модулей,
# чтобы stdout содержал только JSON (CLI invariant).
logging.basicConfig(stream=sys.stderr, level=logging.WARNING, force=True)

# Добавить src/ в sys.path чтобы импортировать модули Krab
_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

# После basicConfig: убеждаемся что все StreamHandler → stderr
_root = logging.getLogger()
for _h in list(_root.handlers):
    if isinstance(_h, logging.StreamHandler) and getattr(_h, "stream", None) is sys.stdout:
        _h.stream = sys.stderr  # перенаправить stdout-handler

# Настроить structlog на WARNING → stderr (до первого get_logger)
try:
    import structlog as _structlog

    _structlog.configure(
        wrapper_class=_structlog.make_filtering_bound_logger(logging.WARNING),
        logger_factory=_structlog.PrintLoggerFactory(file=sys.stderr),
    )
except ImportError:
    pass

from src.core.audit_log_rotator import (  # noqa: E402
    AuditLogRotator,
    _archive_path,
)

_BASH_AUDIT_DEFAULT = "/tmp/krab_bash_audit.log"
_AGENT_AUDIT_DEFAULT = str(Path.home() / ".openclaw" / "krab_runtime_state" / "agent_audit.jsonl")


def _log_info(path: Path, max_mb: int) -> dict:
    """Собрать информацию о логе и его архивах."""
    size_mb = 0.0
    exists = path.exists()
    if exists:
        try:
            size_mb = round(path.stat().st_size / (1024 * 1024), 3)
        except OSError:
            pass

    archives = []
    for idx in range(1, 11):
        arc = _archive_path(path, idx)
        if arc.exists():
            try:
                arc_mb = round(arc.stat().st_size / (1024 * 1024), 3)
            except OSError:
                arc_mb = 0.0
            archives.append({"path": str(arc), "size_mb": arc_mb})
        else:
            break

    return {
        "path": str(path),
        "exists": exists,
        "size_mb": size_mb,
        "would_rotate": size_mb > max_mb,
        "threshold_mb": max_mb,
        "archives": archives,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Wave 56-I: CLI ротация audit-логов Krab",
        allow_abbrev=False,
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true", help="Показать размеры (default)")
    group.add_argument("--force", action="store_true", help="Ротировать без проверки размера")
    parser.add_argument(
        "--max-mb",
        type=int,
        default=int(os.environ.get("KRAB_AUDIT_LOG_MAX_MB", "10")),
        help="Порог ротации в МБ (default 10)",
    )
    parser.add_argument(
        "--keep",
        type=int,
        default=int(os.environ.get("KRAB_AUDIT_LOG_KEEP", "5")),
        help="Количество хранимых архивов (default 5)",
    )
    args = parser.parse_args()

    bash_path = Path(os.environ.get("KRAB_BASH_AUDIT_PATH", _BASH_AUDIT_DEFAULT))
    agent_path = Path(os.environ.get("KRAB_AGENT_AUDIT_PATH", _AGENT_AUDIT_DEFAULT))

    rotator = AuditLogRotator()

    if args.force:
        # Ротировать принудительно: временно установить max_mb=0
        bash_result = rotator.rotate_if_needed(bash_path, max_size_mb=0, keep_count=args.keep)
        agent_result = rotator.rotate_if_needed(agent_path, max_size_mb=0, keep_count=args.keep)
        output = {
            "mode": "force",
            "bash": bash_result,
            "agent": agent_result,
        }
    else:
        # --check (default)
        bash_info = _log_info(bash_path, args.max_mb)
        agent_info = _log_info(agent_path, args.max_mb)
        output = {
            "mode": "check",
            "bash": bash_info,
            "agent": agent_info,
        }

    print(json.dumps(output, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
