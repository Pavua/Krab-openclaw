# -*- coding: utf-8 -*-
"""Тесты для operator_info_guard — PII-redaction для guest/partial контекста."""

from __future__ import annotations

import pytest

from src.core.operator_info_guard import sanitize_for_context

# ───────────────────────── SSH keys ──────────────────────────


@pytest.mark.parametrize(
    "raw_key",
    [
        "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQDabc123xyz user@host",
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIabcdef0123456789xyz me@laptop",
        "ssh-ecdsa AAAAE2VjZHNhLXNoYTItbmlzdHAyNTYAAAAIbmlzdHAyNTYAAAB",
        "SSH-RSA AAAAB3NzaC1yc2EAAAADAQABAAABAQCupper user@host",  # case-insensitive
    ],
)
def test_ssh_key_redacted(raw_key: str) -> None:
    text = f"Here is my key: {raw_key} — keep it safe."
    sanitized, cats = sanitize_for_context(text)
    assert "[REDACTED_SSH_KEY]" in sanitized
    assert "AAAA" not in sanitized
    assert "ssh_key" in cats


# ──────────────────────── Private GitHub ─────────────────────


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/Pavua/secret-repo",
        "http://github.com/Pavua/Krab",
        "github.com/Pavua/openclaw",
        "https://github.com/pablito/private_stuff",
        "GitHub.com/PAVUA/UpperCased",  # case-insensitive
    ],
)
def test_private_github_redacted(url: str) -> None:
    text = f"Check out {url} for details."
    sanitized, cats = sanitize_for_context(text)
    assert "[REDACTED_PRIVATE_REPO]" in sanitized
    assert "Pavua" not in sanitized and "pablito" not in sanitized.lower().replace("redacted", "")
    assert "private_repo" in cats


# ─────────────────────── Home paths ──────────────────────────


@pytest.mark.parametrize(
    "path",
    [
        "/Users/pablito/Antigravity_AGENTS/Краб/src",
        "/Users/pablito/.ssh/id_rsa",
        "/Users/pablito/Documents/notes.md",
    ],
)
def test_home_path_redacted(path: str) -> None:
    text = f"File at {path} matters."
    sanitized, cats = sanitize_for_context(text)
    assert "[REDACTED_PATH]" in sanitized
    assert "/Users/pablito/" not in sanitized
    assert "home_path" in cats


# ───────────────────── Workspace paths ──────────────────────


@pytest.mark.parametrize(
    "path",
    [
        ".openclaw/agents/main/agent/models.json",
        ".openclaw/krab_runtime_state/swarm_channels.json",
        ".openclaw/secrets/api_keys",
    ],
)
def test_workspace_path_redacted(path: str) -> None:
    text = f"See {path} for config."
    sanitized, cats = sanitize_for_context(text)
    assert "[REDACTED_WORKSPACE]" in sanitized
    assert "workspace_path" in cats


# ───────────────────── Combined payload ─────────────────────


def test_combined_redaction() -> None:
    text = (
        "Hi! My key is ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQDabc123def456 me@host. "
        "Repo: https://github.com/Pavua/topsecret, "
        "file /Users/pablito/secrets.txt, "
        "workspace .openclaw/agents/main, "
        "email pavel@example.com."
    )
    sanitized, cats = sanitize_for_context(text)
    assert "[REDACTED_SSH_KEY]" in sanitized
    assert "[REDACTED_PRIVATE_REPO]" in sanitized
    assert "[REDACTED_PATH]" in sanitized
    assert "[REDACTED_WORKSPACE]" in sanitized
    assert "pavel@example.com" not in sanitized  # base PIIRedactor
    for expected in ("ssh_key", "private_repo", "home_path", "workspace_path"):
        assert expected in cats


# ──────────────────── Base PIIRedactor fallback ─────────────


@pytest.mark.parametrize(
    "payload,fragment",
    [
        ("Email me at foo.bar@example.com please", "foo.bar@example.com"),
        ("Phone +14155552671 urgent", "+14155552671"),
        ("Anthropic key sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890abcdef", "sk-ant-api03"),
        ("BTC 1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2", "1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2"),
    ],
)
def test_pii_redactor_fallback(payload: str, fragment: str) -> None:
    sanitized, cats = sanitize_for_context(payload)
    assert fragment not in sanitized
    assert len(cats) >= 1


# ────────────────────── Edge cases ──────────────────────────


def test_none_input() -> None:
    # Не должен падать на None-like (пустом) вводе.
    sanitized, cats = sanitize_for_context("")
    assert sanitized == ""
    assert cats == []


def test_empty_string() -> None:
    sanitized, cats = sanitize_for_context("")
    assert sanitized == ""
    assert cats == []


def test_clean_text_unchanged() -> None:
    clean = "Привет, как дела? Сегодня хорошая погода."
    sanitized, cats = sanitize_for_context(clean)
    assert sanitized == clean
    assert cats == []


def test_categories_list_accurate() -> None:
    # В тексте только ssh+github → в categories только они (+ никаких path/workspace).
    text = "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQDsample123456 user@a https://github.com/Pavua/x"
    _, cats = sanitize_for_context(text)
    assert "ssh_key" in cats
    assert "private_repo" in cats
    assert "home_path" not in cats
    assert "workspace_path" not in cats


def test_context_guest_vs_partial() -> None:
    # По документации behavior одинаковый — проверяем что оба контекста работают без ошибок.
    text = "/Users/pablito/secret.txt"
    guest_res, guest_cats = sanitize_for_context(text, context="guest")
    partial_res, partial_cats = sanitize_for_context(text, context="partial")
    assert guest_res == partial_res
    assert guest_cats == partial_cats
    assert "home_path" in guest_cats
