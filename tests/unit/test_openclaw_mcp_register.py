"""Тесты для scripts/openclaw_mcp_register.py."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Загружаем модуль динамически, поскольку scripts/ не пакет
ROOT = Path(__file__).resolve().parents[2]
SPEC_PATH = ROOT / "scripts" / "openclaw_mcp_register.py"
INVENTORY_PATH = ROOT / "scripts" / "mcp_inventory.toml"

_spec = importlib.util.spec_from_file_location("openclaw_mcp_register", SPEC_PATH)
assert _spec is not None and _spec.loader is not None
mcp_register = importlib.util.module_from_spec(_spec)
sys.modules["openclaw_mcp_register"] = mcp_register
_spec.loader.exec_module(mcp_register)


# ---------------------------------------------------------------------------
# load_inventory + TOML parsing
# ---------------------------------------------------------------------------


def test_load_inventory_real_file_has_expected_servers() -> None:
    inv = mcp_register.load_inventory(INVENTORY_PATH)
    # Sanity: ключевые серверы из задания должны быть
    expected = {"context7", "github", "firecrawl", "sentry", "linear",
                "supabase", "notion", "slack", "atlassian", "asana",
                "figma", "gitlab", "stripe", "firebase", "planetscale",
                "huggingface", "cloudflare"}
    assert expected.issubset(inv.keys()), f"Missing: {expected - inv.keys()}"


def test_load_inventory_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(mcp_register.RegisterError, match="not found"):
        mcp_register.load_inventory(tmp_path / "nope.toml")


def test_load_inventory_parses_minimal_spec(tmp_path: Path) -> None:
    f = tmp_path / "inv.toml"
    f.write_text(
        '[srv]\n'
        'description = "x"\n'
        'transport = "http"\n'
        'url = "https://example.com/mcp"\n'
        'required_env = []\n',
        encoding="utf-8",
    )
    inv = mcp_register.load_inventory(f)
    assert inv["srv"]["transport"] == "http"
    assert inv["srv"]["url"] == "https://example.com/mcp"


# ---------------------------------------------------------------------------
# resolve_env_placeholders
# ---------------------------------------------------------------------------


def test_resolve_env_placeholder_substitutes_when_present() -> None:
    out = mcp_register.resolve_env_placeholders(
        "Bearer ${TOKEN}", {"TOKEN": "abc"}, keep_placeholder=False
    )
    assert out == "Bearer abc"


def test_resolve_env_placeholder_keeps_when_missing_and_flag_true() -> None:
    out = mcp_register.resolve_env_placeholders("Bearer ${X}", {}, keep_placeholder=True)
    assert out == "Bearer ${X}"


def test_resolve_env_placeholder_raises_when_missing_and_flag_false() -> None:
    with pytest.raises(mcp_register.RegisterError, match="Missing env var"):
        mcp_register.resolve_env_placeholders(
            "Bearer ${X}", {}, keep_placeholder=False
        )


def test_resolve_env_placeholder_recurses_into_dict_and_list() -> None:
    spec = {"h": {"Auth": "Bearer ${T}"}, "args": ["--key=${T}"]}
    out = mcp_register.resolve_env_placeholders(spec, {"T": "v"}, keep_placeholder=False)
    assert out == {"h": {"Auth": "Bearer v"}, "args": ["--key=v"]}


# ---------------------------------------------------------------------------
# has_required_tokens
# ---------------------------------------------------------------------------


def test_has_required_tokens_all_present() -> None:
    spec = {"required_env": ["A", "B"]}
    ok, missing = mcp_register.has_required_tokens(spec, {"A": "1", "B": "2"})
    assert ok is True
    assert missing == []


def test_has_required_tokens_some_missing() -> None:
    spec = {"required_env": ["A", "B", "C"]}
    ok, missing = mcp_register.has_required_tokens(spec, {"A": "1", "B": ""})
    assert ok is False
    assert set(missing) == {"B", "C"}


def test_has_required_tokens_empty_list() -> None:
    ok, missing = mcp_register.has_required_tokens({}, {})
    assert ok is True
    assert missing == []


# ---------------------------------------------------------------------------
# build_openclaw_payload
# ---------------------------------------------------------------------------


def test_build_payload_http_aliased_to_streamable_http() -> None:
    """`transport=http` мапится в OpenClaw-совместимое `streamable-http`."""
    spec = {
        "transport": "http",
        "url": "https://api.x.com/mcp",
        "headers": {"Authorization": "Bearer ${TOKEN}"},
        "description": "ignored meta",
        "required_env": ["TOKEN"],
    }
    payload = mcp_register.build_openclaw_payload(spec)
    assert payload == {
        "transport": "streamable-http",
        "url": "https://api.x.com/mcp",
        "headers": {"Authorization": "Bearer ${TOKEN}"},
    }
    # description / required_env не должны попадать в payload
    assert "description" not in payload
    assert "required_env" not in payload


def test_build_payload_streamable_http_passthrough() -> None:
    spec = {
        "transport": "streamable-http",
        "url": "https://x.com/mcp",
    }
    payload = mcp_register.build_openclaw_payload(spec)
    assert payload["transport"] == "streamable-http"


def test_build_payload_stdio() -> None:
    spec = {"transport": "stdio", "command": "npx", "args": ["-y", "@x/y"]}
    payload = mcp_register.build_openclaw_payload(spec)
    assert payload == {"command": "npx", "args": ["-y", "@x/y"]}


def test_build_payload_sse_no_headers() -> None:
    spec = {"transport": "sse", "url": "http://127.0.0.1:8014/sse"}
    payload = mcp_register.build_openclaw_payload(spec)
    assert payload == {"transport": "sse", "url": "http://127.0.0.1:8014/sse"}


def test_build_payload_unknown_transport_raises() -> None:
    with pytest.raises(mcp_register.RegisterError, match="Unknown transport"):
        mcp_register.build_openclaw_payload({"transport": "ws"})


def test_build_payload_http_missing_url() -> None:
    with pytest.raises(mcp_register.RegisterError, match="requires 'url'"):
        mcp_register.build_openclaw_payload({"transport": "streamable-http"})


# ---------------------------------------------------------------------------
# register_one — dry_run + subprocess mocking
# ---------------------------------------------------------------------------


def test_register_one_dry_run_returns_plan_no_subprocess() -> None:
    payload = {"transport": "http", "url": "https://x.com/mcp"}
    with patch("subprocess.run") as run:
        msg = mcp_register.register_one("github", payload, dry_run=True)
    assert run.call_count == 0
    assert "[dry-run]" in msg
    assert "github" in msg
    # JSON в plan валиден
    json_part = msg.split("'", 1)[1].rsplit("'", 1)[0]
    assert json.loads(json_part) == payload


def test_register_one_invokes_openclaw_set() -> None:
    payload = {"transport": "http", "url": "https://x.com/mcp"}
    with patch("subprocess.run") as run:
        run.return_value = type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        msg = mcp_register.register_one("github", payload, dry_run=False)
    args = run.call_args.args[0]
    assert args[:3] == ["openclaw", "mcp", "set"]
    assert args[3] == "github"
    assert json.loads(args[4]) == payload
    assert "registered" in msg


def test_register_one_failure_raises_register_error() -> None:
    import subprocess

    payload = {"transport": "http", "url": "https://x.com/mcp"}
    err = subprocess.CalledProcessError(1, ["openclaw"], stderr="bad json")
    with patch("subprocess.run", side_effect=err):
        with pytest.raises(mcp_register.RegisterError, match="bad json"):
            mcp_register.register_one("x", payload, dry_run=False)


# ---------------------------------------------------------------------------
# list_registered parsing
# ---------------------------------------------------------------------------


def test_list_registered_parses_dash_lines() -> None:
    output = (
        "MCP servers (/Users/x/.openclaw/openclaw.json):\n"
        "- krab-telegram\n"
        "- krab-tor\n"
        "- github\n"
        "[noise] some warning\n"
    )
    fake = type("R", (), {"returncode": 0, "stdout": output, "stderr": ""})()
    with patch("subprocess.run", return_value=fake):
        names = mcp_register.list_registered()
    assert names == ["krab-telegram", "krab-tor", "github"]


# ---------------------------------------------------------------------------
# cmd_add — token gating + idempotency
# ---------------------------------------------------------------------------


def test_cmd_add_skips_when_token_missing(capsys: pytest.CaptureFixture[str]) -> None:
    inv = {
        "github": {
            "transport": "http",
            "url": "https://x.com",
            "headers": {"Authorization": "Bearer ${GITHUB_PERSONAL_ACCESS_TOKEN}"},
            "required_env": ["GITHUB_PERSONAL_ACCESS_TOKEN"],
        }
    }
    rc = mcp_register.cmd_add("github", inv, {}, dry_run=True, openclaw_bin="openclaw")
    assert rc == 3
    err = capsys.readouterr().err
    assert "missing env" in err.lower()


def test_cmd_add_unknown_name_returns_2(capsys: pytest.CaptureFixture[str]) -> None:
    rc = mcp_register.cmd_add("nope", {}, {}, dry_run=True, openclaw_bin="openclaw")
    assert rc == 2
    assert "not in inventory" in capsys.readouterr().err


def test_cmd_add_dry_run_with_token_succeeds(capsys: pytest.CaptureFixture[str]) -> None:
    inv = {
        "github": {
            "transport": "http",
            "url": "https://x.com",
            "headers": {"Authorization": "Bearer ${TOK}"},
            "required_env": ["TOK"],
        }
    }
    rc = mcp_register.cmd_add(
        "github", inv, {"TOK": "abc"}, dry_run=True, openclaw_bin="openclaw"
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "[dry-run]" in out


def test_idempotency_add_all_runs_against_already_registered() -> None:
    """add-all-with-tokens должен переустанавливать существующие без ошибки."""
    inv = {
        "context7": {
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "@upstash/context7-mcp"],
            "required_env": [],
        }
    }
    fake_list = type("R", (), {"returncode": 0, "stdout": "- context7\n", "stderr": ""})()
    fake_set = type("R", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()
    with patch("subprocess.run", side_effect=[fake_list, fake_set]):
        rc = mcp_register.cmd_add_all_with_tokens(
            inv, {}, dry_run=False, openclaw_bin="openclaw"
        )
    assert rc == 0


def test_cmd_add_all_with_tokens_skips_no_token(
    capsys: pytest.CaptureFixture[str],
) -> None:
    inv = {
        "github": {
            "transport": "http",
            "url": "https://x.com",
            "required_env": ["GITHUB_PERSONAL_ACCESS_TOKEN"],
        },
        "context7": {
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "@upstash/context7-mcp"],
            "required_env": [],
        },
    }
    fake_list = type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
    fake_set = type("R", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()
    with patch("subprocess.run", side_effect=[fake_list, fake_set]):
        rc = mcp_register.cmd_add_all_with_tokens(
            inv, {}, dry_run=False, openclaw_bin="openclaw"
        )
    out = capsys.readouterr().out
    assert rc == 0
    assert "context7" in out
    assert "github" in out
    assert "Skipped" in out


# ---------------------------------------------------------------------------
# argparse smoke
# ---------------------------------------------------------------------------


def test_parser_requires_action_group() -> None:
    parser = mcp_register.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_parser_list_flag() -> None:
    args = mcp_register.build_parser().parse_args(["--list"])
    assert args.list is True


def test_parser_add_arg() -> None:
    args = mcp_register.build_parser().parse_args(["--add", "github"])
    assert args.add == "github"
