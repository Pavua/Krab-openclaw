#!/usr/bin/env python3
"""Wave 209: sanity-checker для всех ai.krab.*.plist в LaunchAgents.

Проверяет:
  1. plist XML валиден (plutil -lint)
  2. Label совпадает с именем файла
  3. ProgramArguments[0] существует
  4. Пути в ProgramArguments[1:] существуют (если выглядят как файлы)
  5. Родители StandardOut/StandardError path существуют
  6. Хотя бы один trigger: StartInterval / StartCalendarInterval / RunAtLoad / KeepAlive
  7. WorkingDirectory существует (если указан)
  8. EnvironmentVariables — dict с string-значениями
  9. Лейблы уникальны между всеми plist-ами

Использование:
    python3 scripts/krab_validate_plists.py [--fix-soft] [--strict]

Отчёт: ~/.openclaw/krab_runtime_state/plist_validation.json
"""

from __future__ import annotations

import argparse
import json
import plistlib
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPORT_DIR = Path("~/.openclaw/krab_runtime_state").expanduser()
REPORT_FILE = REPORT_DIR / "plist_validation.json"

CANONICAL_DIR = Path(__file__).resolve().parent / "launchagents"
INSTALLED_DIR = Path("~/Library/LaunchAgents").expanduser()


@dataclass
class PlistResult:
    path: str
    label: str | None = None
    ok: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    fixed: list[str] = field(default_factory=list)

    def err(self, msg: str) -> None:
        self.errors.append(msg)
        self.ok = False

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)


def _plutil_lint(path: Path) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            ["/usr/bin/plutil", "-lint", str(path)],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return False, f"plutil failure: {exc}"
    return proc.returncode == 0, (proc.stdout + proc.stderr).strip()


def _looks_like_path(s: str) -> bool:
    # script-like aргумент: absolute path с .py/.sh или просто abs
    return s.startswith("/") and ("/" in s)


def validate_plist(path: Path, *, fix_soft: bool = False) -> PlistResult:
    result = PlistResult(path=str(path))

    # 1. plutil -lint
    ok, msg = _plutil_lint(path)
    if not ok:
        result.err(f"plutil -lint failed: {msg}")
        return result  # дальнейшие проверки бессмысленны

    # parse
    try:
        with path.open("rb") as f:
            data = plistlib.load(f)
    except Exception as exc:
        result.err(f"plistlib.load failed: {exc}")
        return result

    if not isinstance(data, dict):
        result.err("top-level plist must be a dict")
        return result

    # 2. Label vs filename
    label = data.get("Label")
    result.label = label if isinstance(label, str) else None
    expected_label = path.stem  # ai.krab.foo
    if not isinstance(label, str):
        result.err("Label key missing or not a string")
    elif label != expected_label:
        result.err(f"Label '{label}' does not match filename stem '{expected_label}'")

    # 3+4. ProgramArguments
    prog = data.get("ProgramArguments")
    if not isinstance(prog, list) or not prog:
        result.err("ProgramArguments missing or empty")
    else:
        interpreter = prog[0]
        if not isinstance(interpreter, str):
            result.err("ProgramArguments[0] is not a string")
        elif not Path(interpreter).exists():
            result.err(f"ProgramArguments[0] interpreter not found: {interpreter}")
        # 4. остальные похожие на пути
        for i, arg in enumerate(prog[1:], start=1):
            if isinstance(arg, str) and _looks_like_path(arg):
                # отфильтровываем модульные импорты типа "-m"
                if not Path(arg).exists():
                    result.err(f"ProgramArguments[{i}] path not found: {arg}")

    # 5. StandardOut/StandardError parent dirs
    for key in ("StandardOutPath", "StandardErrorPath"):
        value = data.get(key)
        if value is None:
            continue
        if not isinstance(value, str):
            result.err(f"{key} is not a string")
            continue
        parent = Path(value).expanduser().parent
        if not parent.exists():
            if fix_soft:
                try:
                    parent.mkdir(parents=True, exist_ok=True)
                    result.fixed.append(f"created parent dir for {key}: {parent}")
                except OSError as exc:
                    result.err(f"{key} parent missing and mkdir failed: {parent} ({exc})")
            else:
                result.warn(f"{key} parent dir missing: {parent}")

    # 6. trigger
    triggers = ("StartInterval", "StartCalendarInterval", "RunAtLoad", "KeepAlive", "WatchPaths")
    if not any(k in data for k in triggers):
        result.err(
            f"no trigger key found (expected one of {triggers})"
        )

    # 7. WorkingDirectory
    wd = data.get("WorkingDirectory")
    if isinstance(wd, str) and not Path(wd).expanduser().exists():
        result.err(f"WorkingDirectory does not exist: {wd}")

    # 8. EnvironmentVariables
    env = data.get("EnvironmentVariables")
    if env is not None:
        if not isinstance(env, dict):
            result.err("EnvironmentVariables must be a dict")
        else:
            for k, v in env.items():
                if not isinstance(k, str):
                    result.err(f"EnvironmentVariables key not a string: {k!r}")
                if not isinstance(v, str):
                    result.err(f"EnvironmentVariables[{k}] value not a string: {v!r}")

    return result


def collect_plist_files() -> list[Path]:
    """Канонические + установленные ai.krab.*.plist (deduped по пути)."""
    seen: set[Path] = set()
    files: list[Path] = []
    for base in (CANONICAL_DIR, INSTALLED_DIR):
        if not base.exists():
            continue
        for p in sorted(base.glob("ai.krab.*.plist")):
            rp = p.resolve()
            if rp in seen:
                continue
            seen.add(rp)
            files.append(p)
    return files


def detect_duplicate_labels(results: list[PlistResult]) -> dict[str, list[str]]:
    """Возвращает map label → [paths] для лейблов, встреченных >1 раз
    в РАЗНЫХ файлах (имя файла отличается; копии canonical↔installed ок)."""
    by_label: dict[str, list[str]] = defaultdict(list)
    for r in results:
        if r.label:
            by_label[r.label].append(r.path)
    duplicates: dict[str, list[str]] = {}
    for label, paths in by_label.items():
        filenames = {Path(p).name for p in paths}
        if len(filenames) > 1:
            duplicates[label] = paths
    return duplicates


def build_report(results: list[PlistResult], duplicates: dict[str, list[str]]) -> dict[str, Any]:
    total = len(results)
    ok = sum(1 for r in results if r.ok and not r.warnings)
    with_warnings = sum(1 for r in results if r.ok and r.warnings)
    failed = sum(1 for r in results if not r.ok)
    return {
        "summary": {
            "total": total,
            "ok": ok,
            "with_warnings": with_warnings,
            "failed": failed,
            "duplicate_labels": len(duplicates),
        },
        "duplicate_labels": duplicates,
        "results": [
            {
                "path": r.path,
                "label": r.label,
                "ok": r.ok,
                "errors": r.errors,
                "warnings": r.warnings,
                "fixed": r.fixed,
            }
            for r in results
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fix-soft", action="store_true", help="auto-create missing log dirs")
    parser.add_argument("--strict", action="store_true", help="exit 1 if any errors")
    parser.add_argument(
        "--report", default=str(REPORT_FILE), help="path to JSON report"
    )
    parser.add_argument(
        "--paths", nargs="*", help="override plist paths (testing)"
    )
    args = parser.parse_args(argv)

    if args.paths:
        files = [Path(p) for p in args.paths]
    else:
        files = collect_plist_files()

    results = [validate_plist(p, fix_soft=args.fix_soft) for p in files]
    duplicates = detect_duplicate_labels(results)
    if duplicates:
        # отметить как ошибку в каждом затронутом результате
        for r in results:
            if r.label in duplicates:
                r.err(f"duplicate label across files: {duplicates[r.label]}")

    report = build_report(results, duplicates)

    out = Path(args.report).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False))

    s = report["summary"]
    print(
        f"plist validate: total={s['total']} ok={s['ok']} "
        f"warnings={s['with_warnings']} failed={s['failed']} "
        f"dup_labels={s['duplicate_labels']}"
    )
    for r in results:
        if r.errors or r.warnings:
            print(f"  {Path(r.path).name}:")
            for e in r.errors:
                print(f"    ERROR: {e}")
            for w in r.warnings:
                print(f"    warn:  {w}")
            for f in r.fixed:
                print(f"    fixed: {f}")

    if args.strict and report["summary"]["failed"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
