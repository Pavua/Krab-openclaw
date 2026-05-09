"""Wave 45-C-tools — tests for github/cloudflare/sentry/brave bash tools.

All HTTP / subprocess calls are mocked — no real API hits.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS_DIR = REPO_ROOT / "scripts" / "agent_tools"
PYTHON = sys.executable

sys.path.insert(0, str(TOOLS_DIR))


def _run_subprocess(
    script: str, args: list[str], env_extra: dict[str, str] | None = None, timeout: int = 15
) -> tuple[int, dict | None, str]:
    """Run script as subprocess, return (rc, last-json-line, stderr)."""
    env = {**os.environ}
    if env_extra is not None:
        # Strip CLOUDFLARE/SENTRY/BRAVE tokens from inherited env if env_extra
        # is meant to override. We always start from os.environ minus these
        # specific keys, then layer env_extra.
        for key in (
            "CLOUDFLARE_API_TOKEN",
            "SENTRY_AUTH_TOKEN",
            "BRAVE_SEARCH_API_KEY",
            "SENTRY_ORG_SLUG",
        ):
            env.pop(key, None)
        env.update(env_extra)
    proc = subprocess.run(
        [PYTHON, str(TOOLS_DIR / script), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )
    parsed: dict | None = None
    out = proc.stdout.strip()
    if out:
        for line in reversed(out.splitlines()):
            try:
                parsed = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
    return proc.returncode, parsed, proc.stderr


# ---------- krab_brave ----------


def test_brave_missing_token_returns_2():
    rc, out, _ = _run_subprocess(
        "krab_brave.py", ["search", "--query", "x"], env_extra={}
    )
    assert rc == 2
    assert out is not None and out["ok"] is False
    assert "BRAVE_SEARCH_API_KEY" in out["error"]


def test_brave_search_mocked():
    import krab_brave  # noqa: PLC0415

    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = {
        "web": {
            "results": [
                {"title": "T1", "url": "https://x", "description": "d", "age": None},
                {"title": "T2", "url": "https://y", "description": "d2", "age": "1d"},
            ]
        }
    }
    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_client.__exit__.return_value = False
    fake_client.get.return_value = fake_resp

    args = MagicMock(query="krab", count=2)
    with patch.object(krab_brave, "_client", return_value=fake_client):
        result = krab_brave.cmd_search(args, token="t")
    assert result["ok"] is True
    assert result["count"] == 2
    assert result["results"][0]["title"] == "T1"


def test_brave_search_http_error():
    import krab_brave  # noqa: PLC0415

    fake_resp = MagicMock()
    fake_resp.status_code = 429
    fake_resp.text = "rate limited"
    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_client.__exit__.return_value = False
    fake_client.get.return_value = fake_resp

    args = MagicMock(query="x", count=5)
    with patch.object(krab_brave, "_client", return_value=fake_client):
        result = krab_brave.cmd_search(args, token="t")
    assert result["ok"] is False
    assert "429" in result["error"]


def test_brave_count_clamped_to_max():
    import krab_brave  # noqa: PLC0415

    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = {"web": {"results": []}}
    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_client.__exit__.return_value = False
    fake_client.get.return_value = fake_resp

    args = MagicMock(query="x", count=999)
    with patch.object(krab_brave, "_client", return_value=fake_client):
        krab_brave.cmd_search(args, token="t")
    # The captured params should have count <= 20
    called_params = fake_client.get.call_args.kwargs["params"]
    assert called_params["count"] <= 20


# ---------- krab_cloudflare ----------


def test_cloudflare_missing_token_returns_2():
    rc, out, _ = _run_subprocess(
        "krab_cloudflare.py", ["zones", "list"], env_extra={}
    )
    assert rc == 2
    assert out is not None and "CLOUDFLARE_API_TOKEN" in out["error"]


def test_cloudflare_zones_list_mocked():
    import krab_cloudflare  # noqa: PLC0415

    fake_payload = {
        "ok": True,
        "data": [
            {"id": "abc", "name": "example.com", "status": "active"},
            {"id": "def", "name": "krab.io", "status": "active"},
        ],
        "result_info": {"total_count": 2},
    }
    args = MagicMock(limit=50)
    with patch.object(krab_cloudflare, "_api_get", return_value=fake_payload):
        result = krab_cloudflare.cmd_zones_list(args, token="t")
    assert result["ok"] is True
    assert result["count"] == 2
    assert result["zones"][0]["name"] == "example.com"


def test_cloudflare_dns_list_propagates_error():
    import krab_cloudflare  # noqa: PLC0415

    fake_err = {"ok": False, "error": "HTTP 401"}
    args = MagicMock(zone="z1", limit=100)
    with patch.object(krab_cloudflare, "_api_get", return_value=fake_err):
        result = krab_cloudflare.cmd_dns_list(args, token="t")
    assert result["ok"] is False
    assert "401" in result["error"]


def test_cloudflare_kv_list_namespaces_mocked():
    import krab_cloudflare  # noqa: PLC0415

    fake = {"ok": True, "data": [{"id": "n1", "title": "kv-prod"}], "result_info": None}
    args = MagicMock(account="acc", limit=50)
    with patch.object(krab_cloudflare, "_api_get", return_value=fake):
        result = krab_cloudflare.cmd_kv_list_namespaces(args, token="t")
    assert result["ok"] is True
    assert result["namespaces"][0]["title"] == "kv-prod"


# ---------- krab_sentry ----------


def test_sentry_missing_token_returns_2():
    rc, out, _ = _run_subprocess(
        "krab_sentry.py",
        ["issues", "--project", "p"],
        env_extra={},
    )
    assert rc == 2
    assert out is not None and "SENTRY_AUTH_TOKEN" in out["error"]


def test_sentry_issues_mocked():
    import krab_sentry  # noqa: PLC0415

    fake = {
        "ok": True,
        "data": [
            {
                "id": "1",
                "shortId": "PROJ-1",
                "title": "Boom",
                "status": "unresolved",
                "level": "error",
                "count": 3,
                "userCount": 2,
                "lastSeen": "2026-05-09",
            }
        ],
    }
    args = MagicMock(org="krab", project="proj", limit=10, query="is:unresolved")
    with patch.object(krab_sentry, "_api_request", return_value=fake):
        result = krab_sentry.cmd_issues(args, token="t")
    assert result["ok"] is True
    assert result["count"] == 1
    assert result["issues"][0]["shortId"] == "PROJ-1"


def test_sentry_issues_org_required():
    import krab_sentry  # noqa: PLC0415

    args = MagicMock(org=None, project="p", limit=10, query=None)
    with patch.object(krab_sentry, "_get_default_org", return_value=None):
        result = krab_sentry.cmd_issues(args, token="t")
    assert result["ok"] is False
    assert "org" in result["error"].lower()


def test_sentry_resolve_mocked():
    import krab_sentry  # noqa: PLC0415

    fake = {"ok": True, "data": {"id": "42", "status": "resolved"}}
    args = MagicMock(issue="42")
    with patch.object(krab_sentry, "_api_request", return_value=fake) as p:
        result = krab_sentry.cmd_resolve(args, token="t")
    assert result["ok"] is True
    assert result["status"] == "resolved"
    p.assert_called_once()
    # Verify it was a PUT
    assert p.call_args.args[1] == "PUT"


# ---------- krab_github ----------


def test_github_no_gh_cli_returns_2():
    """Если gh не найден — graceful exit 2."""
    import krab_github  # noqa: PLC0415

    with patch.object(krab_github, "_gh_path", return_value=None):
        rc = krab_github.main(["repo", "--owner", "a", "--name", "b"])
    assert rc == 2


def test_github_repo_mocked():
    import krab_github  # noqa: PLC0415

    fake_json = '{"name":"krab","owner":{"login":"pavua"},"description":"x"}'
    args = MagicMock(owner="pavua", name="krab")
    with patch.object(krab_github, "_run_gh", return_value=(0, fake_json, "")):
        result = krab_github.cmd_repo(args)
    assert result["ok"] is True
    assert result["repo"]["name"] == "krab"


def test_github_issue_list_mocked():
    import krab_github  # noqa: PLC0415

    fake_json = '[{"number":1,"title":"bug","state":"open"},{"number":2,"title":"f","state":"closed"}]'
    args = MagicMock(owner="pavua", name="krab", limit=10)
    with patch.object(krab_github, "_run_gh", return_value=(0, fake_json, "")):
        result = krab_github.cmd_issue_list(args)
    assert result["ok"] is True
    assert result["count"] == 2


def test_github_pr_create_failure():
    import krab_github  # noqa: PLC0415

    args = MagicMock(
        owner="o", name="r", title="T", body="B", head="feat", base="main", draft=False
    )
    with patch.object(
        krab_github, "_run_gh", return_value=(1, "", "remote rejected")
    ):
        result = krab_github.cmd_pr_create(args)
    assert result["ok"] is False
    assert "rejected" in result["error"]


def test_github_actions_runs_mocked():
    import krab_github  # noqa: PLC0415

    fake_json = '[{"databaseId":111,"name":"CI","status":"completed","conclusion":"success"}]'
    args = MagicMock(owner="o", name="r", limit=5)
    with patch.object(krab_github, "_run_gh", return_value=(0, fake_json, "")):
        result = krab_github.cmd_actions_runs(args)
    assert result["ok"] is True
    assert result["count"] == 1
    assert result["runs"][0]["conclusion"] == "success"


def test_github_release_latest_mocked():
    import krab_github  # noqa: PLC0415

    fake_json = '{"tagName":"v1.2.3","name":"1.2.3","isLatest":true}'
    args = MagicMock(owner="o", name="r")
    with patch.object(krab_github, "_run_gh", return_value=(0, fake_json, "")):
        result = krab_github.cmd_release_latest(args)
    assert result["ok"] is True
    assert result["release"]["tagName"] == "v1.2.3"


# ---------- env-loading isolation ----------


def test_brave_env_picked_up_from_dotenv(tmp_path, monkeypatch):
    """Token from .env should be detected by _get_token."""
    import krab_brave  # noqa: PLC0415

    fake_env = tmp_path / ".env"
    fake_env.write_text('BRAVE_SEARCH_API_KEY="abc123"\n', encoding="utf-8")
    monkeypatch.setattr(krab_brave, "REPO_ROOT", tmp_path)
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)

    assert krab_brave._get_token() == "abc123"


@pytest.mark.parametrize(
    "module_name,token_env",
    [
        ("krab_cloudflare", "CLOUDFLARE_API_TOKEN"),
        ("krab_brave", "BRAVE_SEARCH_API_KEY"),
    ],
)
def test_token_dotenv_overrides_env(module_name, token_env, tmp_path, monkeypatch):
    """matches _common._load_env precedence: .env values shadow os.environ."""
    mod = __import__(module_name)
    fake_env = tmp_path / ".env"
    fake_env.write_text(f'{token_env}="from-dotenv"\n', encoding="utf-8")
    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
    monkeypatch.setenv(token_env, "from-env")

    # Behavior matches `{**os.environ, **_load_dotenv()}` — dotenv wins.
    assert mod._get_token() == "from-dotenv"


def test_token_env_only(tmp_path, monkeypatch):
    """When .env absent, env var works alone."""
    import krab_cloudflare  # noqa: PLC0415

    monkeypatch.setattr(krab_cloudflare, "REPO_ROOT", tmp_path)
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "only-env")
    assert krab_cloudflare._get_token() == "only-env"
