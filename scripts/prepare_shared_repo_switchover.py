#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Готовит безопасный switchover-report между текущей копией `pablito` и shared repo.

Что делает:
1) Сравнивает branch/HEAD и локальные dirty paths двух рабочих копий.
2) Генерирует JSON + Markdown report в `artifacts/ops`.
3) При наличии локального WIP пишет patch-файлы для текущей и shared копии,
   но ничего не применяет автоматически.

Зачем:
- быстрое переключение между учётками должно быть воспроизводимым;
- автоматический sync при двух dirty worktree слишком рискован;
- patch/report безопаснее: они сокращают ручную работу, но не ломают git-истину.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.shared_repo_switchover import build_shared_repo_switchover_report


CANONICAL_SHARED_ROOT = Path("/Users/Shared/Antigravity_AGENTS/Краб")
OPS_DIR = ROOT / "artifacts" / "ops"


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _run_git_diff(repo_root: Path) -> str:
    """Возвращает бинарный patch относительно HEAD или пустую строку."""
    try:
        proc = subprocess.run(
            ["git", "diff", "--binary", "HEAD"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
    except Exception:
        return ""
    if proc.returncode != 0:
        return ""
    return proc.stdout or ""


def _write_patch_family(prefix: str, repo_root: Path, stamp: str) -> dict[str, Any]:
    """Пишет versioned/latest patch для указанной рабочей копии."""
    patch = _run_git_diff(repo_root)
    latest_path = OPS_DIR / f"{prefix}_latest.patch"
    versioned_path = OPS_DIR / f"{prefix}_{stamp}.patch"
    if not patch.strip():
        return {
            "exists": False,
            "latest_path": str(latest_path),
            "versioned_path": str(versioned_path),
        }
    _write_text(latest_path, patch)
    _write_text(versioned_path, patch)
    return {
        "exists": True,
        "latest_path": str(latest_path),
        "versioned_path": str(versioned_path),
    }


def _build_markdown(report: dict[str, Any], *, patch_paths: dict[str, Any]) -> str:
    """Строит короткий operator-facing Markdown report."""
    current_repo = dict(report.get("current_repo") or {})
    shared_repo = dict(report.get("shared_repo") or {})
    recommendation = dict(report.get("recommendation") or {})
    overlap_analysis = dict(report.get("overlap_analysis") or {})
    overlap_counts = dict(overlap_analysis.get("counts") or {})
    current_branch = str(current_repo.get("branch") or "unknown")
    shared_branch = str(shared_repo.get("branch") or "unknown")
    current_head = str(current_repo.get("head") or "unknown")
    shared_head = str(shared_repo.get("head") or "unknown")

    lines = [
        "# Shared Repo Switchover Report",
        "",
        f"- Generated (UTC): `{datetime.now(timezone.utc).isoformat(timespec='seconds')}`",
        f"- Current repo: `{ROOT}`",
        f"- Shared repo: `{CANONICAL_SHARED_ROOT}`",
        "",
        "## Branch / HEAD",
        f"- current: `{current_branch}` @ `{current_head}`",
        f"- shared: `{shared_branch}` @ `{shared_head}`",
        f"- strategy: `{recommendation.get('strategy_code', 'unknown')}`",
        f"- readiness: `{recommendation.get('readiness', 'unknown')}`",
        f"- summary: {recommendation.get('summary', '-')}",
        "",
        "## Dirty paths",
        f"- current dirty: `{current_repo.get('dirty_count', 0)}`",
        f"- shared dirty: `{shared_repo.get('dirty_count', 0)}`",
        f"- overlap: `{recommendation.get('overlap_count', 0)}`",
        f"- current-only: `{recommendation.get('current_only_count', 0)}`",
        f"- shared-only: `{recommendation.get('shared_only_count', 0)}`",
    ]

    if overlap_counts:
        lines.extend(
            [
                "",
                "## Overlap content analysis",
                f"- identical: `{overlap_counts.get('identical', 0)}`",
                f"- divergent_text: `{overlap_counts.get('divergent_text', 0)}`",
                f"- divergent_binary: `{overlap_counts.get('divergent_binary', 0)}`",
                f"- only_current: `{overlap_counts.get('only_current', 0)}`",
                f"- only_shared: `{overlap_counts.get('only_shared', 0)}`",
            ]
        )

    overlap_preview = recommendation.get("overlap_paths_preview") or []
    if overlap_preview:
        lines.extend(["", "## Overlap preview"])
        for path in overlap_preview:
            lines.append(f"- `{path}`")

    overlap_items = overlap_analysis.get("items") or []
    divergent_text_items = [
        item for item in overlap_items
        if isinstance(item, dict) and item.get("category") == "divergent_text"
    ]
    if divergent_text_items:
        lines.extend(["", "## Divergent text preview"])
        for item in divergent_text_items[:12]:
            numstat = item.get("numstat") if isinstance(item.get("numstat"), dict) else {}
            added = numstat.get("added")
            deleted = numstat.get("deleted")
            lines.append(
                f"- `{item.get('path')}`: +{added if added is not None else '?'} / -{deleted if deleted is not None else '?'}"
            )

    identical_items = [
        item for item in overlap_items
        if isinstance(item, dict) and item.get("category") == "identical"
    ]
    if identical_items:
        lines.extend(["", "## Identical overlap preview"])
        for item in identical_items[:12]:
            lines.append(f"- `{item.get('path')}`")

    current_only_preview = recommendation.get("current_only_paths_preview") or []
    if current_only_preview:
        lines.extend(["", "## Current-only preview"])
        for path in current_only_preview[:12]:
            lines.append(f"- `{path}`")

    shared_only_preview = recommendation.get("shared_only_paths_preview") or []
    if shared_only_preview:
        lines.extend(["", "## Shared-only preview"])
        for path in shared_only_preview[:12]:
            lines.append(f"- `{path}`")

    lines.extend(["", "## Recommended actions"])
    for action in recommendation.get("actions") or []:
        lines.append(f"- {action}")

    pablito_patch = patch_paths.get("pablito") or {}
    shared_patch = patch_paths.get("shared") or {}
    if pablito_patch.get("exists"):
        lines.extend(
            [
                "",
                "## Carry current WIP into shared repo",
                f"- patch: `{pablito_patch.get('latest_path')}`",
                "```bash",
                f"cd '{CANONICAL_SHARED_ROOT}'",
                f"git switch '{current_branch}' || git switch -c '{current_branch}'",
                f"git apply --3way '{pablito_patch.get('latest_path')}'",
                "git status --short --branch",
                "```",
            ]
        )

    if shared_patch.get("exists"):
        lines.extend(
            [
                "",
                "## Carry shared WIP back into pablito",
                f"- patch: `{shared_patch.get('latest_path')}`",
                "```bash",
                f"cd '{ROOT}'",
                f"git switch '{shared_branch}' || git switch -c '{shared_branch}'",
                f"git apply --3way '{shared_patch.get('latest_path')}'",
                "git status --short --branch",
                "```",
            ]
        )

    lines.extend(
        [
            "",
            "## Guardrails",
            "- Не применять patch автоматически поверх overlap paths без review.",
            "- Не запускать live runtime, пока не понятен owner runtime и не выровнен repo drift.",
            "- Не лечить drift через `git reset --hard` или массовый `chown`.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    OPS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")

    report = build_shared_repo_switchover_report(
        current_root=ROOT,
        shared_root=CANONICAL_SHARED_ROOT,
    )
    patch_paths = {
        "pablito": _write_patch_family("pablito_wip", ROOT, stamp),
        "shared": _write_patch_family("shared_repo_wip", CANONICAL_SHARED_ROOT, stamp)
        if CANONICAL_SHARED_ROOT.exists()
        else {"exists": False},
    }
    report["patches"] = patch_paths

    latest_json = OPS_DIR / "shared_repo_switchover_latest.json"
    versioned_json = OPS_DIR / f"shared_repo_switchover_{stamp}.json"
    latest_md = OPS_DIR / "shared_repo_switchover_latest.md"
    versioned_md = OPS_DIR / f"shared_repo_switchover_{stamp}.md"

    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    _write_text(latest_json, payload)
    _write_text(versioned_json, payload)

    markdown = _build_markdown(report, patch_paths=patch_paths)
    _write_text(latest_md, markdown)
    _write_text(versioned_md, markdown)

    print("=== Shared Repo Switchover ===")
    print(f"strategy: {((report.get('recommendation') or {}).get('strategy_code') or 'unknown')}")
    print(f"readiness: {((report.get('recommendation') or {}).get('readiness') or 'unknown')}")
    print(f"summary: {((report.get('recommendation') or {}).get('summary') or '-')}")
    print(f"latest_json: {latest_json}")
    print(f"latest_md: {latest_md}")
    if patch_paths.get("pablito", {}).get("exists"):
        print(f"pablito_patch: {patch_paths['pablito']['latest_path']}")
    if patch_paths.get("shared", {}).get("exists"):
        print(f"shared_patch: {patch_paths['shared']['latest_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
