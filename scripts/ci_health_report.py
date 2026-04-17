#!/usr/bin/env python3
"""
CI Health Report — aggregates pytest + ruff + coverage + file metrics
into a Markdown report для быстрой оценки кодовой базы.

Usage:
    python scripts/ci_health_report.py                    # print to stdout
    python scripts/ci_health_report.py --output report.md # to file
    python scripts/ci_health_report.py --quick            # skip slow checks (coverage)
"""
import argparse
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _resolve_venv_bin(name: str) -> Path:
    """Ищем venv/bin/<name> в worktree или в родительском репо (для git worktrees)."""
    local = REPO / "venv" / "bin" / name
    if local.exists():
        return local
    # Worktree: поднимаемся выше, ищем "Краб/venv/bin/<name>"
    for candidate in (REPO.parent.parent.parent, REPO.parent.parent, REPO.parent):
        guess = candidate / "venv" / "bin" / name
        if guess.exists():
            return guess
    return local  # fallback — пусть упадёт с понятной ошибкой


VENV_PY = _resolve_venv_bin("python")
VENV_RUFF = _resolve_venv_bin("ruff")


def run(cmd: list[str], timeout: int = 120) -> tuple[int, str, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=REPO)
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"TIMEOUT after {timeout}s"
    except FileNotFoundError as e:
        return 127, "", str(e)


def ruff_check():
    code, out, err = run([str(VENV_RUFF), "check", "src/", "tests/"])
    errors = 0
    m = re.search(r"Found (\d+) error", out + err)
    if m:
        errors = int(m.group(1))
    elif code != 0:
        # Нет точного парсинга — считаем строки с указанием файла
        errors = sum(1 for line in out.splitlines() if re.match(r"^\S+\.py:\d+:", line))
    return {
        "passed": code == 0,
        "errors": errors,
        "raw_exit": code,
    }


def pytest_summary(quick: bool = False):
    args = [str(VENV_PY), "-m", "pytest", "tests/", "--tb=no", "-q"]
    if quick:
        args.append("-x")
    code, out, err = run(args, timeout=600)

    text = out + "\n" + err
    passed = re.search(r"(\d+) passed", text)
    failed = re.search(r"(\d+) failed", text)
    skipped = re.search(r"(\d+) skipped", text)
    errors = re.search(r"(\d+) error", text)

    return {
        "passed": int(passed.group(1)) if passed else 0,
        "failed": int(failed.group(1)) if failed else 0,
        "skipped": int(skipped.group(1)) if skipped else 0,
        "errors": int(errors.group(1)) if errors else 0,
        "exit_ok": code == 0,
    }


def coverage_summary():
    try:
        code, out, err = run(
            [
                str(VENV_PY),
                "-m",
                "pytest",
                "tests/unit/",
                "--tb=no",
                "-q",
                "--cov=src",
                "--cov-report=term",
                "--no-cov-on-fail",
            ],
            timeout=600,
        )
    except Exception as e:
        return {"error": str(e)}

    for line in out.split("\n"):
        if line.strip().startswith("TOTAL"):
            parts = line.split()
            if len(parts) >= 4 and "%" in parts[-1]:
                try:
                    return {
                        "total_stmts": int(parts[1]),
                        "missed": int(parts[2]),
                        "pct": parts[-1],
                    }
                except ValueError:
                    pass
    return {"pct": "unknown"}


def file_metrics():
    src = REPO / "src"
    tests = REPO / "tests"
    py_files = []
    if src.exists():
        py_files += list(src.rglob("*.py"))
    if tests.exists():
        py_files += list(tests.rglob("*.py"))
    total_lines = 0
    for p in py_files:
        try:
            with p.open("r", encoding="utf-8", errors="ignore") as f:
                total_lines += sum(1 for _ in f)
        except Exception:
            pass
    return {
        "py_files": len(py_files),
        "total_lines": total_lines,
    }


def git_summary():
    code, out, _ = run(["git", "log", "--oneline", "-n", "10"])
    commits = out.strip().split("\n") if out.strip() else []
    code2, branch_out, _ = run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    branch = branch_out.strip() if code2 == 0 else "?"
    return {"recent": commits, "branch": branch}


def make_report(ruff_r, pytest_r, cov_r, files_r, git_r):
    t = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ruff_cell = "clean" if ruff_r["passed"] else f"{ruff_r['errors']} errors"
    lines = [
        "# CI Health Report — Krab",
        "",
        f"Generated: {t}",
        f"Branch: `{git_r['branch']}`",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Ruff | {ruff_cell} |",
        f"| Pytest | {pytest_r['passed']} passed / {pytest_r['failed']} failed / {pytest_r['skipped']} skipped |",
        f"| Coverage | {cov_r.get('pct', 'n/a')} |",
        f"| Python files | {files_r['py_files']} |",
        f"| Total lines | {files_r['total_lines']:,} |",
        "",
    ]

    if pytest_r["failed"]:
        lines.append(f"**{pytest_r['failed']} failing tests** — investigation needed.")
        lines.append("")

    if ruff_r["errors"]:
        lines.append(f"**{ruff_r['errors']} lint errors** — run `ruff check --fix src/`.")
        lines.append("")

    lines.append("## Recent commits")
    lines.append("")
    for c in git_r["recent"]:
        lines.append(f"- `{c}`")
    lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=str)
    parser.add_argument("--quick", action="store_true")
    args = parser.parse_args()

    print("Running ruff...", file=sys.stderr)
    ruff_r = ruff_check()

    print("Running pytest...", file=sys.stderr)
    pytest_r = pytest_summary(quick=args.quick)

    if args.quick:
        cov_r = {"pct": "skipped (--quick)"}
    else:
        print("Running coverage...", file=sys.stderr)
        cov_r = coverage_summary()

    print("File metrics...", file=sys.stderr)
    files_r = file_metrics()

    print("Git summary...", file=sys.stderr)
    git_r = git_summary()

    report = make_report(ruff_r, pytest_r, cov_r, files_r, git_r)

    if args.output:
        Path(args.output).write_text(report)
        print(f"Written to {args.output}", file=sys.stderr)
    else:
        print(report)


if __name__ == "__main__":
    main()
