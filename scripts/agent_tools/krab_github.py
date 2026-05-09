#!/usr/bin/env python3
"""Wave 45-C-tools — GitHub bash interface через `gh` CLI.

Подкоманды: repo, issue (list/get/create), pr (list/get/create/comment),
actions (runs), release (latest).

Использует существующий `gh` PAT из ~/.zshrc (GITHUB_PERSONAL_ACCESS_TOKEN).
JSON output. exit codes: 0 ok / 1 error / 2 missing tool.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import emit_error, emit_json  # noqa: E402

SCRIPT = "krab_github.py"


def _gh_path() -> str | None:
    return shutil.which("gh")


def _run_gh(args: list[str], timeout: int = 30) -> tuple[int, str, str]:
    """Запускаем `gh ...`. Возвращаем (rc, stdout, stderr)."""
    gh = _gh_path()
    if not gh:
        return 127, "", "gh CLI not found in PATH"
    proc = subprocess.run(  # noqa: S603
        [gh, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def _parse_json(stdout: str) -> object:
    """Пытаемся распарсить stdout как JSON, иначе возвращаем raw string."""
    try:
        return json.loads(stdout) if stdout else None
    except json.JSONDecodeError:
        return stdout


def cmd_repo(args: argparse.Namespace) -> dict:
    slug = f"{args.owner}/{args.name}"
    rc, out, err = _run_gh(
        [
            "repo",
            "view",
            slug,
            "--json",
            "name,owner,description,url,defaultBranchRef,stargazerCount,visibility",
        ]
    )
    if rc != 0:
        return {"ok": False, "error": err or "gh repo view failed"}
    return {"ok": True, "repo": _parse_json(out)}


def cmd_issue_list(args: argparse.Namespace) -> dict:
    repo = f"{args.owner}/{args.name}"
    rc, out, err = _run_gh(
        [
            "issue",
            "list",
            "--repo",
            repo,
            "--limit",
            str(args.limit),
            "--json",
            "number,title,state,author,createdAt,labels",
        ]
    )
    if rc != 0:
        return {"ok": False, "error": err or "gh issue list failed"}
    issues = _parse_json(out) or []
    return {"ok": True, "repo": repo, "count": len(issues) if isinstance(issues, list) else 0, "issues": issues}


def cmd_issue_get(args: argparse.Namespace) -> dict:
    repo = f"{args.owner}/{args.name}"
    rc, out, err = _run_gh(
        [
            "issue",
            "view",
            str(args.number),
            "--repo",
            repo,
            "--json",
            "number,title,state,body,author,createdAt,closedAt,labels,comments",
        ]
    )
    if rc != 0:
        return {"ok": False, "error": err or "gh issue view failed"}
    return {"ok": True, "issue": _parse_json(out)}


def cmd_issue_create(args: argparse.Namespace) -> dict:
    repo = f"{args.owner}/{args.name}"
    cmd = ["issue", "create", "--repo", repo, "--title", args.title, "--body", args.body or ""]
    if args.label:
        for lbl in args.label:
            cmd.extend(["--label", lbl])
    rc, out, err = _run_gh(cmd)
    if rc != 0:
        return {"ok": False, "error": err or "gh issue create failed"}
    return {"ok": True, "url": out.strip()}


def cmd_pr_list(args: argparse.Namespace) -> dict:
    repo = f"{args.owner}/{args.name}"
    rc, out, err = _run_gh(
        [
            "pr",
            "list",
            "--repo",
            repo,
            "--limit",
            str(args.limit),
            "--json",
            "number,title,state,author,createdAt,headRefName,baseRefName",
        ]
    )
    if rc != 0:
        return {"ok": False, "error": err or "gh pr list failed"}
    prs = _parse_json(out) or []
    return {"ok": True, "repo": repo, "count": len(prs) if isinstance(prs, list) else 0, "prs": prs}


def cmd_pr_get(args: argparse.Namespace) -> dict:
    repo = f"{args.owner}/{args.name}"
    rc, out, err = _run_gh(
        [
            "pr",
            "view",
            str(args.number),
            "--repo",
            repo,
            "--json",
            "number,title,state,body,author,createdAt,mergedAt,headRefName,baseRefName,additions,deletions",
        ]
    )
    if rc != 0:
        return {"ok": False, "error": err or "gh pr view failed"}
    return {"ok": True, "pr": _parse_json(out)}


def cmd_pr_create(args: argparse.Namespace) -> dict:
    repo = f"{args.owner}/{args.name}"
    cmd = [
        "pr",
        "create",
        "--repo",
        repo,
        "--title",
        args.title,
        "--body",
        args.body or "",
        "--head",
        args.head,
        "--base",
        args.base,
    ]
    if args.draft:
        cmd.append("--draft")
    rc, out, err = _run_gh(cmd)
    if rc != 0:
        return {"ok": False, "error": err or "gh pr create failed"}
    return {"ok": True, "url": out.strip()}


def cmd_pr_comment(args: argparse.Namespace) -> dict:
    repo = f"{args.owner}/{args.name}"
    rc, out, err = _run_gh(
        ["pr", "comment", str(args.number), "--repo", repo, "--body", args.body]
    )
    if rc != 0:
        return {"ok": False, "error": err or "gh pr comment failed"}
    return {"ok": True, "url": out.strip()}


def cmd_actions_runs(args: argparse.Namespace) -> dict:
    repo = f"{args.owner}/{args.name}"
    rc, out, err = _run_gh(
        [
            "run",
            "list",
            "--repo",
            repo,
            "--limit",
            str(args.limit),
            "--json",
            "databaseId,name,status,conclusion,headBranch,createdAt,workflowName",
        ]
    )
    if rc != 0:
        return {"ok": False, "error": err or "gh run list failed"}
    runs = _parse_json(out) or []
    return {"ok": True, "repo": repo, "count": len(runs) if isinstance(runs, list) else 0, "runs": runs}


def cmd_release_latest(args: argparse.Namespace) -> dict:
    repo = f"{args.owner}/{args.name}"
    rc, out, err = _run_gh(
        [
            "release",
            "view",
            "--repo",
            repo,
            "--json",
            "tagName,name,body,createdAt,publishedAt,url,isLatest,isPrerelease",
        ]
    )
    if rc != 0:
        return {"ok": False, "error": err or "gh release view failed"}
    return {"ok": True, "release": _parse_json(out)}


def _add_repo_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--owner", required=True)
    p.add_argument("--name", required=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="GitHub bash interface (через gh CLI)")
    parser.add_argument("--json", action="store_true", help="output JSON (default)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    repo_p = sub.add_parser("repo", help="инфо о репо")
    _add_repo_args(repo_p)

    issue_p = sub.add_parser("issue", help="issues operations")
    issue_sub = issue_p.add_subparsers(dest="action", required=True)
    il = issue_sub.add_parser("list")
    _add_repo_args(il)
    il.add_argument("--limit", type=int, default=30)
    ig = issue_sub.add_parser("get")
    _add_repo_args(ig)
    ig.add_argument("--number", type=int, required=True)
    ic = issue_sub.add_parser("create")
    _add_repo_args(ic)
    ic.add_argument("--title", required=True)
    ic.add_argument("--body", default="")
    ic.add_argument("--label", action="append")

    pr_p = sub.add_parser("pr", help="pull request operations")
    pr_sub = pr_p.add_subparsers(dest="action", required=True)
    pl = pr_sub.add_parser("list")
    _add_repo_args(pl)
    pl.add_argument("--limit", type=int, default=30)
    pg = pr_sub.add_parser("get")
    _add_repo_args(pg)
    pg.add_argument("--number", type=int, required=True)
    pc = pr_sub.add_parser("create")
    _add_repo_args(pc)
    pc.add_argument("--title", required=True)
    pc.add_argument("--body", default="")
    pc.add_argument("--head", required=True)
    pc.add_argument("--base", default="main")
    pc.add_argument("--draft", action="store_true")
    pcm = pr_sub.add_parser("comment")
    _add_repo_args(pcm)
    pcm.add_argument("--number", type=int, required=True)
    pcm.add_argument("--body", required=True)

    actions_p = sub.add_parser("actions", help="GitHub Actions")
    actions_sub = actions_p.add_subparsers(dest="action", required=True)
    runs = actions_sub.add_parser("runs")
    _add_repo_args(runs)
    runs.add_argument("--limit", type=int, default=20)

    rel_p = sub.add_parser("release", help="releases")
    rel_sub = rel_p.add_subparsers(dest="action", required=True)
    rl = rel_sub.add_parser("latest")
    _add_repo_args(rl)

    args = parser.parse_args(argv)

    if not _gh_path():
        emit_json(
            {"ok": False, "error": "gh CLI not found", "hint": "brew install gh"},
            SCRIPT,
            sys.argv[1:],
        )
        return 2

    handlers = {
        ("repo", None): cmd_repo,
        ("issue", "list"): cmd_issue_list,
        ("issue", "get"): cmd_issue_get,
        ("issue", "create"): cmd_issue_create,
        ("pr", "list"): cmd_pr_list,
        ("pr", "get"): cmd_pr_get,
        ("pr", "create"): cmd_pr_create,
        ("pr", "comment"): cmd_pr_comment,
        ("actions", "runs"): cmd_actions_runs,
        ("release", "latest"): cmd_release_latest,
    }
    key = (args.cmd, getattr(args, "action", None))
    handler = handlers.get(key)
    if handler is None:
        return emit_error(f"unknown subcommand: {key}", SCRIPT, sys.argv[1:])

    try:
        result = handler(args)
    except subprocess.TimeoutExpired:
        return emit_error("gh subprocess timeout", SCRIPT, sys.argv[1:])
    except Exception as exc:  # noqa: BLE001
        return emit_error(f"{type(exc).__name__}: {exc}", SCRIPT, sys.argv[1:])

    emit_json(result, SCRIPT, sys.argv[1:])
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
