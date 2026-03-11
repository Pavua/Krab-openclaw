# -*- coding: utf-8 -*-
"""
Тесты для scripts/openclaw_runtime_repair.py.

Проверяют:
1) Выбор корректного AI Studio ключа.
2) Снятие залипших local-overrides в channel sessions.
3) Нормализацию allowlist.
"""

from __future__ import annotations

import json
from pathlib import Path

from scripts.openclaw_runtime_repair import (
    apply_group_policy,
    apply_dm_policy,
    choose_target_key,
    choose_lmstudio_token,
    detect_active_channels,
    normalize_allowlist,
    repair_cron_jobs,
    repair_output_sanitizer_plugin_config,
    repair_agent_model_overrides,
    repair_channel_health_monitor,
    repair_compaction_memory_flush,
    repair_external_reasoning_defaults,
    repair_group_policy_allowlist,
    repair_imessage_reply_tag_patch,
    repair_lmstudio_provider_catalog,
    repair_main_agent_messaging_profile,
    repair_reply_to_modes,
    repair_hooks_config,
    repair_sessions,
    sync_managed_output_sanitizer_plugin,
    sync_auth_profiles_json,
    sync_models_json,
    sync_openclaw_json,
    should_restart_gateway,
)


def test_choose_target_key_prefers_free_in_auto() -> None:
    tier, key = choose_target_key(
        free_key="AIzaFREE1234567890123456789012345",
        paid_key="AIzaPAID1234567890123456789012345",
        tier="auto",
    )
    assert tier == "free"
    assert key.startswith("AIzaFREE")


def test_choose_target_key_paid_when_free_invalid() -> None:
    tier, key = choose_target_key(
        free_key="AQ.INVALID",
        paid_key="AIzaPAID1234567890123456789012345",
        tier="auto",
    )
    assert tier == "paid"
    assert key.startswith("AIzaPAID")


def test_choose_lmstudio_token_prefers_primary_over_legacy() -> None:
    token = choose_lmstudio_token(
        primary_token="lm-primary-token",
        legacy_token="lm-legacy-token",
    )
    assert token == "lm-primary-token"


def test_sync_openclaw_json_updates_lmstudio_token_when_present(tmp_path: Path) -> None:
    openclaw_path = tmp_path / "openclaw.json"
    openclaw_path.write_text(
        json.dumps(
            {
                "models": {
                    "providers": {
                        "google": {"apiKey": "AIzaOLD1234567890123456789012345"},
                        "lmstudio": {
                            "apiKey": "local-dummy-key",
                            "auth": "api-key",
                            "api": "openai-completions",
                        },
                    }
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = sync_openclaw_json(
        openclaw_path,
        "AIzaNEW1234567890123456789012345",
        "lm-real-token",
    )

    assert report["changed"] is True
    assert report["lmstudio_changed"] is True
    payload = json.loads(openclaw_path.read_text(encoding="utf-8"))
    assert payload["models"]["providers"]["google"]["apiKey"] == "AIzaNEW1234567890123456789012345"
    assert payload["models"]["providers"]["lmstudio"]["apiKey"] == "lm-real-token"


def test_sync_models_json_keeps_existing_lmstudio_token_when_env_missing(tmp_path: Path) -> None:
    models_path = tmp_path / "models.json"
    models_path.write_text(
        json.dumps(
            {
                "providers": {
                    "google": {"apiKey": "AIzaOLD1234567890123456789012345"},
                    "lmstudio": {
                        "apiKey": "lm-existing-token",
                        "auth": "api-key",
                        "api": "openai-completions",
                    },
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = sync_models_json(
        models_path,
        "AIzaNEW1234567890123456789012345",
        "",
    )

    assert report["changed"] is True
    assert report["lmstudio_changed"] is False
    payload = json.loads(models_path.read_text(encoding="utf-8"))
    assert payload["providers"]["google"]["apiKey"] == "AIzaNEW1234567890123456789012345"
    assert payload["providers"]["lmstudio"]["apiKey"] == "lm-existing-token"


def test_sync_managed_output_sanitizer_plugin_copies_repo_files(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    source_dir = repo_root / "plugins" / "krab-output-sanitizer"
    source_dir.mkdir(parents=True)
    (source_dir / "index.mjs").write_text("// managed plugin\n", encoding="utf-8")
    (source_dir / "openclaw.plugin.json").write_text('{"id":"krab-output-sanitizer"}\n', encoding="utf-8")

    openclaw_root = tmp_path / "runtime"

    report = sync_managed_output_sanitizer_plugin(
        openclaw_root=openclaw_root,
        repo_root=repo_root,
    )

    assert report["changed"] is True
    target_dir = openclaw_root / "extensions" / "krab-output-sanitizer"
    assert (target_dir / "index.mjs").read_text(encoding="utf-8") == "// managed plugin\n"
    assert (target_dir / "openclaw.plugin.json").read_text(encoding="utf-8") == '{"id":"krab-output-sanitizer"}\n'


def test_repair_output_sanitizer_plugin_config_enforces_truthful_external_defaults(tmp_path: Path) -> None:
    openclaw_path = tmp_path / "openclaw.json"
    openclaw_path.write_text(
        json.dumps(
            {
                "plugins": {
                    "entries": {
                        "krab-output-sanitizer": {
                            "enabled": False,
                            "config": {
                                "enabled": False,
                                "guestAllowedTools": ["browser", "web_search"],
                                "externalChannelAllowedTools": ["browser", "tts"],
                                "externalChannelGuardEnabled": False,
                                "externalChannelToolGuardEnabled": False,
                            },
                        }
                    }
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = repair_output_sanitizer_plugin_config(openclaw_path)

    assert report["changed"] is True
    payload = json.loads(openclaw_path.read_text(encoding="utf-8"))
    entry = payload["plugins"]["entries"]["krab-output-sanitizer"]
    assert entry["enabled"] is True
    assert entry["config"]["enabled"] is True
    assert entry["config"]["guestModeEnabled"] is True
    assert entry["config"]["guestToolGuardEnabled"] is True
    assert entry["config"]["externalChannelGuardEnabled"] is True
    assert entry["config"]["externalChannelToolGuardEnabled"] is True
    assert entry["config"]["guestAllowedTools"] == ["web_search", "web_fetch", "weather", "time"]
    assert entry["config"]["externalChannelAllowedTools"] == ["web_search", "web_fetch", "weather", "time"]
    assert isinstance(entry["config"]["ownerAliases"], list)
    assert isinstance(entry["config"]["trustedPeers"], dict)


def test_sync_auth_profiles_json_updates_lmstudio_and_google_tokens(tmp_path: Path) -> None:
    auth_profiles_path = tmp_path / "auth-profiles.json"
    auth_profiles_path.write_text(
        json.dumps(
            {
                "google": {"apiKey": "AIzaOLD1234567890123456789012345"},
                "gemini": {"apiKey": "AIzaOLD1234567890123456789012345"},
                "lmstudio": {"apiKey": "local-dummy-key"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = sync_auth_profiles_json(
        auth_profiles_path,
        "AIzaNEW1234567890123456789012345",
        "sk-lm-real-token",
    )

    assert report["changed"] is True
    payload = json.loads(auth_profiles_path.read_text(encoding="utf-8"))
    assert payload["google"]["apiKey"] == "AIzaNEW1234567890123456789012345"
    assert payload["gemini"]["apiKey"] == "AIzaNEW1234567890123456789012345"
    assert payload["lmstudio"]["apiKey"] == "sk-lm-real-token"


def test_repair_lmstudio_provider_catalog_replaces_stale_glm_with_primary_model(tmp_path: Path) -> None:
    models_path = tmp_path / "models.json"
    models_path.write_text(
        json.dumps(
            {
                "providers": {
                    "lmstudio": {
                        "baseUrl": "http://localhost:1234/v1",
                        "apiKey": "lm-real-token",
                        "auth": "api-key",
                        "api": "openai-completions",
                        "models": [
                            {
                                "id": "zai-org/glm-4.6v-flash",
                                "name": "GLM-4.6V Flash (LM Studio)",
                                "input": ["text", "image"],
                                "contextWindow": 32768,
                                "maxTokens": 700,
                                "compat": {"maxTokensField": "max_tokens"},
                            }
                        ],
                    }
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = repair_lmstudio_provider_catalog(
        models_path,
        primary_model="lmstudio/nvidia/nemotron-3-nano",
        preferred_text_model="lmstudio/nvidia/nemotron-3-nano",
        preferred_vision_model="auto",
        lmstudio_token="lm-real-token",
        live_models=[
            {
                "id": "nvidia/nemotron-3-nano",
                "display_name": "Nemotron 3 Nano",
                "supports_vision": False,
                "context_window": 262144,
                "size_bytes": 17790000000,
            },
            {
                "id": "qwen2-vl-2b-instruct-abliterated-mlx",
                "display_name": "Qwen2 VL 2B",
                "supports_vision": True,
                "context_window": 32768,
                "size_bytes": 3230000000,
            },
        ],
    )

    assert report["changed"] is True
    assert report["models"] == ["nvidia/nemotron-3-nano"]
    payload = json.loads(models_path.read_text(encoding="utf-8"))
    catalog = payload["providers"]["lmstudio"]["models"]
    assert [item["id"] for item in catalog] == ["nvidia/nemotron-3-nano"]
    assert catalog[0]["input"] == ["text"]


def test_repair_lmstudio_provider_catalog_does_not_copy_cloud_primary_into_local_catalog(tmp_path: Path) -> None:
    models_path = tmp_path / "models.json"
    models_path.write_text(
        json.dumps(
            {
                "providers": {
                    "lmstudio": {
                        "baseUrl": "http://localhost:1234/v1",
                        "apiKey": "lm-real-token",
                        "auth": "api-key",
                        "api": "openai-completions",
                        "models": [],
                    }
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = repair_lmstudio_provider_catalog(
        models_path,
        primary_model="openai-codex/gpt-5.4",
        preferred_text_model="lmstudio/nvidia/nemotron-3-nano",
        preferred_vision_model="auto",
        lmstudio_token="lm-real-token",
        live_models=[
            {
                "id": "nvidia/nemotron-3-nano",
                "display_name": "Nemotron 3 Nano",
                "supports_vision": False,
                "context_window": 262144,
                "size_bytes": 17790000000,
            },
            {
                "id": "qwen3.5-9b-mlx-vlm",
                "display_name": "Qwen 3.5 9B VLM",
                "supports_vision": True,
                "context_window": 65536,
                "size_bytes": 9300000000,
            },
        ],
    )

    assert report["changed"] is True
    assert report["models"] == ["nvidia/nemotron-3-nano"]
    payload = json.loads(models_path.read_text(encoding="utf-8"))
    catalog = payload["providers"]["lmstudio"]["models"]
    assert [item["id"] for item in catalog] == ["nvidia/nemotron-3-nano"]


def test_repair_sessions_clears_local_overrides(tmp_path: Path) -> None:
    sessions_path = tmp_path / "sessions.json"
    payload = {
        "agent:main:telegram:direct:312322764": {
            "modelOverride": "local",
            "providerOverride": "lmstudio",
            "modelProvider": "lmstudio",
            "model": "local",
        },
        "agent:main:openai:abc": {
            "modelOverride": "local",
            "providerOverride": "lmstudio",
            "modelProvider": "lmstudio",
            "model": "local",
        },
    }
    sessions_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    report = repair_sessions(
        sessions_path,
        channels=("telegram",),
        default_provider="google",
        default_model="google/gemini-2.5-flash",
    )
    assert report["changed"] is True
    assert report["fixed_entries"] == 2

    updated = json.loads(sessions_path.read_text(encoding="utf-8"))
    item = updated["agent:main:telegram:direct:312322764"]
    assert "modelOverride" not in item
    assert "providerOverride" not in item
    assert item["modelProvider"] == "google"
    assert item["model"] == "google/gemini-2.5-flash"

    openai_entry = updated["agent:main:openai:abc"]
    assert "modelOverride" not in openai_entry
    assert "providerOverride" not in openai_entry
    assert openai_entry["modelProvider"] == "google"
    assert openai_entry["model"] == "google/gemini-2.5-flash"


def test_repair_sessions_resets_legacy_owner_bootstrap_metadata(tmp_path: Path) -> None:
    session_file = tmp_path / "direct.jsonl"
    session_file.write_text('{"role":"assistant","content":"ok"}\n', encoding="utf-8")
    sessions_path = tmp_path / "sessions.json"
    payload = {
        "agent:main:telegram:direct:312322764": {
            "sessionId": "abc",
            "sessionFile": "direct.jsonl",
            "systemSent": True,
            "skillsSnapshot": {
                "prompt": "<available_skills>\n/opt/homebrew/lib/node_modules/openclaw/skills/\ncoding-agent\n</available_skills>"
            },
            "systemPromptReport": {
                "workspaceDir": "/Users/pablito/.openclaw/workspace",
            },
            "modelOverride": "google/gemini-2.5-flash",
            "providerOverride": "google",
            "modelProvider": "google",
            "model": "google/gemini-2.5-flash",
        }
    }
    sessions_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    report = repair_sessions(
        sessions_path,
        channels=("telegram",),
        default_provider="lmstudio",
        default_model="lmstudio/nvidia/nemotron-3-nano",
    )

    assert report["changed"] is True
    assert report["reset_legacy_bootstrap_sessions"] == 1
    updated = json.loads(sessions_path.read_text(encoding="utf-8"))
    item = updated["agent:main:telegram:direct:312322764"]
    assert "skillsSnapshot" not in item
    assert "systemPromptReport" not in item
    assert "modelOverride" not in item
    assert "providerOverride" not in item
    assert item["systemSent"] is False
    assert item["modelProvider"] == "lmstudio"
    assert item["model"] == "nvidia/nemotron-3-nano"


def test_repair_sessions_replaces_pinned_local_model_for_channels_and_openai_scope(tmp_path: Path) -> None:
    sessions_path = tmp_path / "sessions.json"
    payload = {
        "agent:main:telegram:direct:1": {
            "modelProvider": "lmstudio",
            "model": "zai-org/glm-4.6v-flash",
        },
        "agent:main:imessage:direct:2": {
            "modelProvider": "lmstudio",
            "model": "nvidia/nemotron-3-nano",
        },
        "agent:main:openai:3": {
            "modelProvider": "lmstudio",
            "model": "zai-org/glm-4.6v-flash",
        },
        "agent:main:main": {
            "modelProvider": "lmstudio",
            "model": "lmstudio/qwen3.5-27b",
        },
    }
    sessions_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    report = repair_sessions(
        sessions_path,
        channels=("telegram", "imessage"),
        default_provider="lmstudio",
        default_model="lmstudio/nvidia/nemotron-3-nano",
    )

    assert report["changed"] is True
    assert report["replaced_channel_local_model"] == 3
    assert report["session_default_model"] == "nvidia/nemotron-3-nano"

    updated = json.loads(sessions_path.read_text(encoding="utf-8"))
    assert updated["agent:main:telegram:direct:1"]["model"] == "nvidia/nemotron-3-nano"
    assert updated["agent:main:telegram:direct:1"]["modelProvider"] == "lmstudio"
    # Уже совпадающая запись не меняется.
    assert updated["agent:main:imessage:direct:2"]["model"] == "nvidia/nemotron-3-nano"
    # Transport/openai и agent:main:main тоже выравниваются.
    assert updated["agent:main:openai:3"]["model"] == "nvidia/nemotron-3-nano"
    assert updated["agent:main:main"]["model"] == "nvidia/nemotron-3-nano"


def test_repair_sessions_replaces_stale_cloud_pins_when_primary_is_local(tmp_path: Path) -> None:
    sessions_path = tmp_path / "sessions.json"
    payload = {
        "agent:main:telegram:direct:1": {
            "modelProvider": "google",
            "model": "google/gemini-2.5-flash",
        },
        "agent:main:imessage:direct:user@example.com": {
            "modelProvider": "google",
            "model": "google/gemini-2.5-flash",
        },
        "agent:main:main": {
            "modelProvider": "google",
            "model": "google/gemini-2.5-flash",
        },
    }
    sessions_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    report = repair_sessions(
        sessions_path,
        channels=("telegram", "imessage"),
        default_provider="lmstudio",
        default_model="lmstudio/nvidia/nemotron-3-nano",
    )

    assert report["changed"] is True
    assert report["replaced_channel_pinned_model"] == 3
    updated = json.loads(sessions_path.read_text(encoding="utf-8"))
    assert updated["agent:main:telegram:direct:1"]["modelProvider"] == "lmstudio"
    assert updated["agent:main:telegram:direct:1"]["model"] == "nvidia/nemotron-3-nano"
    assert updated["agent:main:imessage:direct:user@example.com"]["modelProvider"] == "lmstudio"
    assert updated["agent:main:imessage:direct:user@example.com"]["model"] == "nvidia/nemotron-3-nano"
    assert updated["agent:main:main"]["modelProvider"] == "lmstudio"
    assert updated["agent:main:main"]["model"] == "nvidia/nemotron-3-nano"


def test_should_restart_gateway_when_sessions_changed() -> None:
    steps = {
        "repair_sessions": {"changed": True},
        "sync_openclaw_json": {"changed": False},
    }
    assert should_restart_gateway(steps) is True


def test_should_restart_gateway_when_auth_profiles_changed() -> None:
    steps = {
        "sync_auth_profiles_json": {"changed": True},
        "repair_sessions": {"changed": False},
    }
    assert should_restart_gateway(steps) is True


def test_should_restart_gateway_when_plugin_sync_or_config_changed() -> None:
    steps = {
        "sync_managed_output_sanitizer_plugin": {"changed": True},
        "repair_output_sanitizer_plugin_config": {"changed": False},
    }
    assert should_restart_gateway(steps) is True

    steps = {
        "sync_managed_output_sanitizer_plugin": {"changed": False},
        "repair_output_sanitizer_plugin_config": {"changed": True},
    }
    assert should_restart_gateway(steps) is True


def test_should_restart_gateway_ignores_allowlist_only_changes() -> None:
    steps = {
        "repair_sessions": {"changed": False},
        "normalize_allowlists": {"telegram": {"changed": True}},
    }
    assert should_restart_gateway(steps) is False


def test_normalize_allowlist_removes_wildcards_and_duplicates(tmp_path: Path) -> None:
    allow_path = tmp_path / "imessage-allowFrom.json"
    allow_path.write_text(
        json.dumps(["+", "", "user@example.com", "user@example.com", "*", "+34600000000"], ensure_ascii=False),
        encoding="utf-8",
    )
    report = normalize_allowlist(allow_path)
    assert report["changed"] is True
    result = json.loads(allow_path.read_text(encoding="utf-8"))
    assert result == ["user@example.com", "+34600000000"]


def test_apply_dm_policy_open_adds_wildcard_allow_from(tmp_path: Path) -> None:
    openclaw_path = tmp_path / "openclaw.json"
    openclaw_path.write_text(
        json.dumps(
            {
                "channels": {
                    "telegram": {"enabled": True, "dmPolicy": "open"},
                    "imessage": {"enabled": True, "dmPolicy": "open", "allowFrom": []},
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    report = apply_dm_policy(openclaw_path, ("telegram", "imessage"), "open")
    assert report["changed"] is True
    assert report["allow_from_changes"] == 2
    payload = json.loads(openclaw_path.read_text(encoding="utf-8"))
    assert payload["channels"]["telegram"]["allowFrom"] == ["*"]
    assert payload["channels"]["imessage"]["allowFrom"] == ["*"]


def test_apply_dm_policy_allowlist_replaces_wildcard_with_trusted_peers(tmp_path: Path) -> None:
    openclaw_path = tmp_path / "openclaw.json"
    openclaw_path.write_text(
        json.dumps(
            {
                "channels": {
                    "telegram": {"enabled": True, "dmPolicy": "open", "allowFrom": ["*"]},
                },
                "plugins": {
                    "entries": {
                        "krab-output-sanitizer": {
                            "config": {
                                "trustedPeers": {
                                    "telegram": ["312322764", "trusted_user"],
                                }
                            }
                        }
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = apply_dm_policy(openclaw_path, ("telegram",), "allowlist")
    assert report["changed"] is True
    assert report["allow_from_fixed"]["telegram"] == "derived_from_trusted_peers"

    payload = json.loads(openclaw_path.read_text(encoding="utf-8"))
    assert payload["channels"]["telegram"]["dmPolicy"] == "allowlist"
    assert payload["channels"]["telegram"]["allowFrom"] == ["312322764", "trusted_user"]


def test_apply_group_policy_allowlist_uses_dm_allowlist_for_telegram_senders(tmp_path: Path) -> None:
    openclaw_path = tmp_path / "openclaw.json"
    openclaw_path.write_text(
        json.dumps(
            {
                "channels": {
                    "telegram": {
                        "enabled": True,
                        "groupPolicy": "open",
                        "allowFrom": ["312322764"],
                        "groups": {
                            "-1001804661353": {"enabled": True},
                            "-1001999999999": {"enabled": False},
                        },
                    }
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = apply_group_policy(openclaw_path, ("telegram",), "allowlist")
    assert report["changed"] is True
    assert report["group_allow_from_fixed"]["telegram"] == "derived_from_dm_allowlist"

    payload = json.loads(openclaw_path.read_text(encoding="utf-8"))
    assert payload["channels"]["telegram"]["groupPolicy"] == "allowlist"
    assert payload["channels"]["telegram"]["groupAllowFrom"] == ["312322764"]


def test_apply_group_policy_allowlist_rewrites_invalid_telegram_group_ids(tmp_path: Path) -> None:
    openclaw_path = tmp_path / "openclaw.json"
    openclaw_path.write_text(
        json.dumps(
            {
                "channels": {
                    "telegram": {
                        "enabled": True,
                        "groupPolicy": "allowlist",
                        "allowFrom": ["312322764"],
                        "groupAllowFrom": ["-1001804661353"],
                        "groups": {
                            "-1001804661353": {"enabled": True},
                        },
                    }
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = apply_group_policy(openclaw_path, ("telegram",), "allowlist")
    assert report["changed"] is True
    assert report["group_allow_from_fixed"]["telegram"] == "derived_from_dm_allowlist"

    payload = json.loads(openclaw_path.read_text(encoding="utf-8"))
    assert payload["channels"]["telegram"]["groupAllowFrom"] == ["312322764"]


def test_repair_group_policy_allowlist_switches_to_open_when_empty(tmp_path: Path) -> None:
    openclaw_path = tmp_path / "openclaw.json"
    openclaw_path.write_text(
        json.dumps(
            {
                "channels": {
                    "imessage": {"enabled": True, "groupPolicy": "allowlist", "groupAllowFrom": []},
                    "telegram": {"enabled": True, "groupPolicy": "allowlist", "groupAllowFrom": ["123"]},
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    report = repair_group_policy_allowlist(openclaw_path, ("imessage", "telegram"))
    assert report["changed"] is True
    assert report["channels"] == ["imessage"]

    payload = json.loads(openclaw_path.read_text(encoding="utf-8"))
    assert payload["channels"]["imessage"]["groupPolicy"] == "open"
    assert payload["channels"]["telegram"]["groupPolicy"] == "allowlist"


def test_repair_group_policy_allowlist_switches_telegram_to_open_when_only_group_ids_left(tmp_path: Path) -> None:
    openclaw_path = tmp_path / "openclaw.json"
    openclaw_path.write_text(
        json.dumps(
            {
                "channels": {
                    "telegram": {
                        "enabled": True,
                        "groupPolicy": "allowlist",
                        "groupAllowFrom": ["-1001804661353"],
                        "groups": {
                            "-1001804661353": {"enabled": True},
                        },
                    },
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = repair_group_policy_allowlist(openclaw_path, ("telegram",))
    assert report["changed"] is True
    assert report["channels"] == ["telegram"]

    payload = json.loads(openclaw_path.read_text(encoding="utf-8"))
    assert payload["channels"]["telegram"]["groupPolicy"] == "open"
    assert "groupAllowFrom" not in payload["channels"]["telegram"]


def test_repair_hooks_disables_enabled_without_token(tmp_path: Path) -> None:
    openclaw_path = tmp_path / "openclaw.json"
    openclaw_path.write_text(
        json.dumps({"hooks": {"enabled": True}}, ensure_ascii=False),
        encoding="utf-8",
    )
    report = repair_hooks_config(openclaw_path)
    assert report["changed"] is True
    assert report["action"] == "disabled_hooks_without_token"
    payload = json.loads(openclaw_path.read_text(encoding="utf-8"))
    assert payload["hooks"]["enabled"] is False


def test_repair_compaction_memory_flush_disables_service_flush(tmp_path: Path) -> None:
    openclaw_path = tmp_path / "openclaw.json"
    openclaw_path.write_text(
        json.dumps(
            {"agents": {"defaults": {"compaction": {"memoryFlush": {"enabled": True}}}}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    report = repair_compaction_memory_flush(openclaw_path)
    assert report["changed"] is True
    payload = json.loads(openclaw_path.read_text(encoding="utf-8"))
    assert payload["agents"]["defaults"]["compaction"]["memoryFlush"]["enabled"] is False


def test_repair_channel_health_monitor_disables_aggressive_runtime_restarts(tmp_path: Path) -> None:
    openclaw_path = tmp_path / "openclaw.json"
    openclaw_path.write_text(
        json.dumps(
            {"gateway": {"channelHealthCheckMinutes": 5}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = repair_channel_health_monitor(openclaw_path)

    assert report["changed"] is True
    assert report["previous_minutes"] == 5
    payload = json.loads(openclaw_path.read_text(encoding="utf-8"))
    assert payload["gateway"]["channelHealthCheckMinutes"] == 0


def test_repair_channel_health_monitor_keeps_disabled_state(tmp_path: Path) -> None:
    openclaw_path = tmp_path / "openclaw.json"
    openclaw_path.write_text(
        json.dumps(
            {"gateway": {"channelHealthCheckMinutes": 0}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = repair_channel_health_monitor(openclaw_path)

    assert report["changed"] is False
    payload = json.loads(openclaw_path.read_text(encoding="utf-8"))
    assert payload["gateway"]["channelHealthCheckMinutes"] == 0


def test_repair_external_reasoning_defaults_sets_off_for_primary_and_local(tmp_path: Path) -> None:
    openclaw_path = tmp_path / "openclaw.json"
    openclaw_path.write_text(
        json.dumps(
            {
                "agents": {
                    "defaults": {
                        "thinkingDefault": "high",
                        "models": {
                            "lmstudio/local": {"params": {"thinking": "high"}},
                            "lmstudio/nvidia/nemotron-3-nano": {"params": {"thinking": "auto"}},
                        },
                    }
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = repair_external_reasoning_defaults(
        openclaw_path,
        primary_model="lmstudio/nvidia/nemotron-3-nano",
    )

    assert report["changed"] is True
    payload = json.loads(openclaw_path.read_text(encoding="utf-8"))
    defaults = payload["agents"]["defaults"]
    assert defaults["thinkingDefault"] == "off"
    assert defaults["models"]["lmstudio/local"]["params"]["thinking"] == "off"
    assert defaults["models"]["lmstudio/nvidia/nemotron-3-nano"]["params"]["thinking"] == "off"


def test_repair_main_agent_messaging_profile_sets_safe_tools_and_workspace(tmp_path: Path) -> None:
    openclaw_path = tmp_path / "openclaw.json"
    openclaw_path.write_text(
        json.dumps(
            {
                "tools": {
                    "exec": {"enabled": True},
                    "web": {"enabled": True},
                    "sessions": {"visibility": "all"},
                    "message": {"allowCrossContextSend": True},
                    "agentToAgent": {"enabled": True},
                    "elevated": {"enabled": True},
                },
                "agents": {
                    "defaults": {
                        "workspace": str(tmp_path / "workspace"),
                    },
                    "list": [
                        {
                            "id": "main",
                            "model": "lmstudio/nvidia/nemotron-3-nano",
                            "subagents": {"allowAgents": ["*"]},
                            "tools": {
                                "profile": "full",
                                "sessions": {"visibility": "all"},
                                "message": {"allowCrossContextSend": True},
                                "agentToAgent": {"enabled": True},
                                "elevated": {"enabled": True},
                            },
                        }
                    ],
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = repair_main_agent_messaging_profile(openclaw_path)

    assert report["changed"] is True
    payload = json.loads(openclaw_path.read_text(encoding="utf-8"))
    root_tools = payload["tools"]
    assert "exec" in root_tools
    assert "web" in root_tools
    assert "sessions" not in root_tools
    assert "message" not in root_tools
    assert "agentToAgent" not in root_tools
    assert "elevated" not in root_tools
    assert payload["agents"]["defaults"]["workspace"].endswith("workspace-main-messaging")
    main_agent = payload["agents"]["list"][0]
    assert main_agent["workspace"].endswith("workspace-main-messaging")
    assert main_agent["tools"]["profile"] == "messaging"
    assert "sessions" not in main_agent["tools"]
    assert "message" not in main_agent["tools"]
    assert "agentToAgent" not in main_agent["tools"]
    assert "elevated" not in main_agent["tools"]
    assert "sessions_send" in main_agent["tools"]["deny"]
    assert "sessions_spawn" in main_agent["tools"]["deny"]
    soul_path = Path(main_agent["workspace"]) / "SOUL.md"
    assert soul_path.exists()
    soul_text = soul_path.read_text(encoding="utf-8")
    assert "внешние каналы OpenClaw" in soul_text
    assert "Не заявляй" in soul_text


def test_repair_reply_to_modes_sets_off_for_channels(tmp_path: Path) -> None:
    openclaw_path = tmp_path / "openclaw.json"
    openclaw_path.write_text(
        json.dumps(
            {
                "channels": {
                    "telegram": {"enabled": True},
                    "imessage": {"enabled": True, "replyToMode": "inline"},
                    "whatsapp": {"enabled": True, "replyToMode": "inline"},
                    "slack": {"enabled": True, "replyToMode": "native"},
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = repair_reply_to_modes(openclaw_path, ("telegram", "imessage", "whatsapp", "slack"), "off")
    assert report["changed"] is True
    payload = json.loads(openclaw_path.read_text(encoding="utf-8"))
    assert payload["channels"]["telegram"]["replyToMode"] == "off"
    assert "replyToMode" not in payload["channels"]["imessage"]
    assert "replyToMode" not in payload["channels"]["whatsapp"]
    assert payload["channels"]["slack"]["replyToMode"] == "off"


def test_repair_imessage_reply_tag_patch_patches_send_bundle(tmp_path: Path) -> None:
    dist_dir = tmp_path / "dist"
    plugin_sdk_dir = dist_dir / "plugin-sdk"
    plugin_sdk_dir.mkdir(parents=True)
    send_bundle = dist_dir / "send-imessage.js"
    plugin_send_bundle = plugin_sdk_dir / "send-plugin-imessage.js"
    bundle_body = (
        "function prependReplyTagIfNeeded(message, replyToId) {\n"
        "  if (!replyToId) return message;\n"
        "  const replyTag = `[[reply_to:${replyToId}]]`;\n"
        "  return `${replyTag} ${message}`;\n"
        "}\n"
        "function resolveMessageId(result) {\n"
        "  return result;\n"
        "}\n"
        "function sendMessageIMessage(message, opts) {\n"
        "  message = prependReplyTagIfNeeded(message, opts.replyToId);\n"
        "  return message;\n"
        "}\n"
    )
    send_bundle.write_text(bundle_body, encoding="utf-8")
    plugin_send_bundle.write_text(bundle_body, encoding="utf-8")

    report = repair_imessage_reply_tag_patch(search_roots=(dist_dir,))

    assert report["changed"] is True
    patched = send_bundle.read_text(encoding="utf-8")
    patched_plugin = plugin_send_bundle.read_text(encoding="utf-8")
    assert "function prependReplyTagIfNeeded(message, replyToId)" in patched
    assert "\treturn message;" in patched
    assert "const replyTag" not in patched
    assert 'replace(/^\\s*\\[\\[' in patched
    assert 'message = String(message ?? "").replace(' in patched
    assert "function prependReplyTagIfNeeded(message, replyToId)" in patched_plugin
    assert "\treturn message;" in patched_plugin
    assert 'replace(/^\\s*\\[\\[' in patched_plugin
    assert "Краб: iMessage показывает [[reply_to:*]] как обычный текст" in patched
    assert str(send_bundle) in report["patched_paths"]
    assert str(plugin_send_bundle) in report["patched_paths"]


def test_repair_imessage_reply_tag_patch_upgrades_legacy_noop_line(tmp_path: Path) -> None:
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir(parents=True)
    send_bundle = dist_dir / "send-imessage.js"
    send_bundle.write_text(
        (
            "function prependReplyTagIfNeeded(message, replyToId) {\n"
            "  /* Краб: iMessage показывает [[reply_to:*]] как обычный текст: отключаем reply-tag перед отправкой. */\n"
            "  return message;\n"
            "}\n"
            "function resolveMessageId(result) {\n"
            "  return result;\n"
            "}\n"
            "async function sendMessageIMessage(text, opts) {\n"
            "  let message = text ?? \"\";\n"
            "  message = message; /* Краб: iMessage показывает [[reply_to:*]] как обычный текст: отключаем reply-tag перед отправкой. */\n"
            "  return message;\n"
            "}\n"
        ),
        encoding="utf-8",
    )

    report = repair_imessage_reply_tag_patch(search_roots=(dist_dir,))

    assert report["changed"] is True
    patched = send_bundle.read_text(encoding="utf-8")
    assert 'let message = String(text ?? "").replace(' in patched
    assert 'message = String(message ?? "").replace(' in patched
    assert 'replace(/^\\s*\\[\\[' in patched
    assert "message = message;" not in patched


def test_repair_imessage_reply_tag_patch_normalizes_broken_double_let_bundle(tmp_path: Path) -> None:
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir(parents=True)
    send_bundle = dist_dir / "send-imessage.js"
    send_bundle.write_text(
        (
            "function prependReplyTagIfNeeded(message, replyToId) {\n"
            "  /* Краб: iMessage показывает [[reply_to:*]] как обычный текст: отключаем reply-tag перед отправкой. */\n"
            "  return message;\n"
            "}\n"
            "function resolveMessageId(result) {\n"
            "  return result;\n"
            "}\n"
            "async function sendMessageIMessage(to, text, opts = {}) {\n"
            "  let message = String(message ?? \"\").replace(/^\\s*\\[\\[\\s*(?:reply_to_current|reply_to\\s*:[^\\]]+|reply_to_[^\\]]+)\\s*\\]\\]\\s*/i, \"\"); /* Краб: iMessage показывает [[reply_to:*]] как обычный текст: вырезаем reply-tag из готового текста перед отправкой. */\n"
            "  if (message.trim()) {\n"
            "    message = convertMarkdownTables(message, \"off\");\n"
            "  }\n"
            "  let message = String(message ?? \"\").replace(/^\\s*\\[\\[\\s*(?:reply_to_current|reply_to\\s*:[^\\]]+|reply_to_[^\\]]+)\\s*\\]\\]\\s*/i, \"\"); /* Краб: iMessage показывает [[reply_to:*]] как обычный текст: вырезаем reply-tag из готового текста перед отправкой. */\n"
            "  const params = { text: message };\n"
            "  return params;\n"
            "}\n"
        ),
        encoding="utf-8",
    )

    report = repair_imessage_reply_tag_patch(search_roots=(dist_dir,))

    assert report["changed"] is True
    patched = send_bundle.read_text(encoding="utf-8")
    assert patched.count('let message = String(text ?? "").replace(') == 1
    assert patched.count('message = String(message ?? "").replace(') == 1
    assert 'let message = String(message ?? "").replace(' not in patched


def test_repair_imessage_reply_tag_patch_replaces_function_even_if_marker_exists_elsewhere(tmp_path: Path) -> None:
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir(parents=True)
    send_bundle = dist_dir / "send-imessage.js"
    send_bundle.write_text(
        (
            "function prependReplyTagIfNeeded(message, replyToId) {\n"
            "  if (!replyToId) return message;\n"
            "  const replyTag = `[[reply_to:${replyToId}]]`;\n"
            "  return `${replyTag} ${message}`;\n"
            "}\n"
            "function resolveMessageId(result) {\n"
            "  return result;\n"
            "}\n"
            "async function sendMessageIMessage(text, opts) {\n"
            "  let message = String(text ?? \"\").replace(/^\\s*\\[\\[\\s*(?:reply_to_current|reply_to\\s*:[^\\]]+|reply_to_[^\\]]+)\\s*\\]\\]\\s*/i, \"\"); /* Краб: iMessage показывает [[reply_to:*]] как обычный текст: вырезаем reply-tag из готового текста перед отправкой. */\n"
            "  message = prependReplyTagIfNeeded(message, opts.replyToId);\n"
            "  return message;\n"
            "}\n"
        ),
        encoding="utf-8",
    )

    report = repair_imessage_reply_tag_patch(search_roots=(dist_dir,))

    assert report["changed"] is True
    patched = send_bundle.read_text(encoding="utf-8")
    assert "const replyTag" not in patched
    assert "function prependReplyTagIfNeeded(message, replyToId)" in patched
    assert "\treturn message;" in patched


def test_detect_active_channels_uses_enabled_flag_and_legacy_fallback() -> None:
    payload = {
        "channels": {
            "telegram": {"enabled": True},
            "imessage": {"enabled": False},
            "slack": {"mode": "socket"},
        }
    }
    channels = detect_active_channels(payload)
    assert channels == ("telegram", "slack")


def test_detect_active_channels_fallback_to_defaults_on_empty() -> None:
    channels = detect_active_channels({"channels": {}})
    assert "telegram" in channels
    assert "discord" in channels


def test_repair_agent_model_overrides_replaces_generic_local(tmp_path: Path) -> None:
    openclaw_path = tmp_path / "openclaw.json"
    openclaw_path.write_text(
        json.dumps(
            {
                "agents": {
                    "defaults": {
                        "subagents": {"model": "lmstudio/local"},
                    },
                    "list": [
                        {"id": "main", "model": "lmstudio/local"},
                        {"id": "qa", "model": "google/gemini-2.5-flash"},
                    ],
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = repair_agent_model_overrides(openclaw_path, default_model="google/gemini-2.5-flash")
    assert report["changed"] is True
    assert report["fixed"] == 2

    payload = json.loads(openclaw_path.read_text(encoding="utf-8"))
    assert payload["agents"]["defaults"]["subagents"]["model"] == "google/gemini-2.5-flash"
    assert payload["agents"]["list"][0]["model"] == "google/gemini-2.5-flash"
    assert payload["agents"]["list"][1]["model"] == "google/gemini-2.5-flash"


def test_repair_sessions_resets_polluted_direct_session(tmp_path: Path) -> None:
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    session_file = sessions_dir / "polluted.jsonl"
    session_file.write_text(
        '{"role":"user","content":"Проверка связи"}\n'
        '{"role":"user","content":"Pre-compaction memory flush. Store durable memories now. If nothing to store, reply with NO_REPLY."}\n'
        '{"role":"assistant","content":"NO_REPLY"}\n',
        encoding="utf-8",
    )
    sessions_path = sessions_dir / "sessions.json"
    sessions_path.write_text(
        json.dumps(
            {
                "agent:main:telegram:direct:312322764": {
                    "sessionId": "abc",
                    "sessionFile": "polluted.jsonl",
                    "modelProvider": "lmstudio",
                    "model": "nvidia/nemotron-3-nano",
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = repair_sessions(
        sessions_path,
        channels=("telegram",),
        default_provider="lmstudio",
        default_model="lmstudio/nvidia/nemotron-3-nano",
    )

    assert report["changed"] is True
    assert report["reset_polluted_direct_sessions"] == 1
    payload = json.loads(sessions_path.read_text(encoding="utf-8"))
    assert "agent:main:telegram:direct:312322764" not in payload
    archived = list(sessions_dir.glob("polluted.polluted_*.jsonl"))
    assert len(archived) == 1


def test_repair_sessions_resets_tool_packet_with_reply_tag_pollution(tmp_path: Path) -> None:
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    session_file = sessions_dir / "reply-polluted.jsonl"
    session_file.write_text(
        '{"role":"assistant","content":"[[reply_to:69787]] {"type":"toolResult","path":"heartbeat-state.json"}"}\n'
        '{"role":"assistant","content":"Tool [[reply_to:69787]] not found"}\n',
        encoding="utf-8",
    )
    sessions_path = sessions_dir / "sessions.json"
    sessions_path.write_text(
        json.dumps(
            {
                "agent:main:imessage:direct:pavelr7@me.com": {
                    "sessionId": "reply-polluted",
                    "sessionFile": "reply-polluted.jsonl",
                    "modelProvider": "lmstudio",
                    "model": "nvidia/nemotron-3-nano",
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = repair_sessions(
        sessions_path,
        channels=("imessage",),
        default_provider="lmstudio",
        default_model="lmstudio/nvidia/nemotron-3-nano",
    )

    assert report["changed"] is True
    assert report["reset_polluted_direct_sessions"] == 1
    payload = json.loads(sessions_path.read_text(encoding="utf-8"))
    assert "agent:main:imessage:direct:pavelr7@me.com" not in payload
    archived = list(sessions_dir.glob("reply-polluted.polluted_*.jsonl"))
    assert len(archived) == 1


def test_repair_sessions_resets_tool_packet_with_false_capability_pollution(tmp_path: Path) -> None:
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    session_file = sessions_dir / "capability-polluted.jsonl"
    session_file.write_text(
        '{"role":"toolCall","path":"heartbeat-state.json","action":"read"}\n'
        'browser cron voice tts session_status\n',
        encoding="utf-8",
    )
    sessions_path = sessions_dir / "sessions.json"
    sessions_path.write_text(
        json.dumps(
            {
                "agent:main:telegram:direct:312322764": {
                    "sessionId": "capability-polluted",
                    "sessionFile": "capability-polluted.jsonl",
                    "modelProvider": "lmstudio",
                    "model": "nvidia/nemotron-3-nano",
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = repair_sessions(
        sessions_path,
        channels=("telegram",),
        default_provider="lmstudio",
        default_model="lmstudio/nvidia/nemotron-3-nano",
    )

    assert report["changed"] is True
    assert report["reset_polluted_direct_sessions"] == 1
    payload = json.loads(sessions_path.read_text(encoding="utf-8"))
    assert "agent:main:telegram:direct:312322764" not in payload
    archived = list(sessions_dir.glob("capability-polluted.polluted_*.jsonl"))
    assert len(archived) == 1


def test_repair_sessions_resets_tool_packet_with_false_runtime_selfcheck_claims(tmp_path: Path) -> None:
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    session_file = sessions_dir / "runtime-selfcheck-polluted.jsonl"
    session_file.write_text(
        '{"role":"toolCall","path":"heartbeat-state.json","action":"read"}\n'
        'Мой доступ к твоей вкладке Chrome теперь работает корректно. Я могу использовать браузер.\n'
        'Крон работает. Хардбит настроен.\n',
        encoding="utf-8",
    )
    sessions_path = sessions_dir / "sessions.json"
    sessions_path.write_text(
        json.dumps(
            {
                "agent:main:telegram:direct:312322764": {
                    "sessionId": "runtime-selfcheck-polluted",
                    "sessionFile": "runtime-selfcheck-polluted.jsonl",
                    "modelProvider": "lmstudio",
                    "model": "nvidia/nemotron-3-nano",
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = repair_sessions(
        sessions_path,
        channels=("telegram",),
        default_provider="lmstudio",
        default_model="lmstudio/nvidia/nemotron-3-nano",
    )

    assert report["changed"] is True
    assert report["reset_polluted_direct_sessions"] == 1
    payload = json.loads(sessions_path.read_text(encoding="utf-8"))
    assert "agent:main:telegram:direct:312322764" not in payload
    archived = list(sessions_dir.glob("runtime-selfcheck-polluted.polluted_*.jsonl"))
    assert len(archived) == 1


def test_repair_cron_jobs_disables_broken_announce_without_target(tmp_path: Path) -> None:
    cron_jobs_path = tmp_path / "jobs.json"
    cron_jobs_path.write_text(
        json.dumps(
            {
                "version": 1,
                "jobs": [
                    {
                        "id": "broken-announce",
                        "enabled": True,
                        "delivery": {"mode": "announce"},
                        "state": {"lastError": "Delivering to WhatsApp requires target <E.164|group JID>"},
                    },
                    {
                        "id": "healthy",
                        "enabled": True,
                        "delivery": {"mode": "announce", "target": "12345@g.us"},
                        "state": {"lastError": ""},
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = repair_cron_jobs(cron_jobs_path)

    assert report["changed"] is True
    assert report["disabled_invalid_announce_jobs"] == 1
    assert report["disabled_job_ids"] == ["broken-announce"]
    payload = json.loads(cron_jobs_path.read_text(encoding="utf-8"))
    assert payload["jobs"][0]["enabled"] is False
    assert payload["jobs"][1]["enabled"] is True


def test_repair_cron_jobs_disables_orphan_system_event_without_delivery_target(tmp_path: Path) -> None:
    cron_jobs_path = tmp_path / "jobs.json"
    cron_jobs_path.write_text(
        json.dumps(
            {
                "version": 1,
                "jobs": [
                    {
                        "id": "orphan-system-event",
                        "enabled": True,
                        "payload": {"kind": "systemEvent"},
                        "state": {"lastDeliveryStatus": "not-requested"},
                    },
                    {
                        "id": "system-event-with-target",
                        "enabled": True,
                        "payload": {"kind": "systemEvent"},
                        "delivery": {"target": "12345@g.us"},
                        "state": {"lastDeliveryStatus": "not-requested"},
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = repair_cron_jobs(cron_jobs_path)

    assert report["changed"] is True
    assert report["disabled_orphan_system_jobs"] == 1
    assert report["disabled_orphan_job_ids"] == ["orphan-system-event"]
    payload = json.loads(cron_jobs_path.read_text(encoding="utf-8"))
    assert payload["jobs"][0]["enabled"] is False
    assert payload["jobs"][1]["enabled"] is True


def test_repair_sessions_removes_orphaned_telegram_slash_alias(tmp_path: Path) -> None:
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    session_file = sessions_dir / "direct.jsonl"
    session_file.write_text('{"role":"assistant","content":"ok"}\n', encoding="utf-8")
    sessions_path = sessions_dir / "sessions.json"
    sessions_path.write_text(
        json.dumps(
            {
                "telegram:slash:312322764": {
                    "sessionId": "missing-openai-session",
                },
                "agent:main:telegram:direct:312322764": {
                    "sessionId": "ok",
                    "sessionFile": "direct.jsonl",
                    "modelProvider": "lmstudio",
                    "model": "nvidia/nemotron-3-nano",
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = repair_sessions(
        sessions_path,
        channels=("telegram",),
        default_provider="lmstudio",
        default_model="lmstudio/nvidia/nemotron-3-nano",
    )

    assert report["changed"] is True
    assert report["reset_broken_transport_aliases"] == 1
    payload = json.loads(sessions_path.read_text(encoding="utf-8"))
    assert "telegram:slash:312322764" not in payload
    assert "agent:main:telegram:direct:312322764" in payload


def test_repair_sessions_resets_polluted_openai_session_and_linked_alias(tmp_path: Path) -> None:
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    session_file = sessions_dir / "transport-session.jsonl"
    session_file.write_text(
        '{"role":"assistant","content":"Connection error."}\n'
        '{"role":"user","content":"[Current message - respond to this] Проверка связи"}\n',
        encoding="utf-8",
    )
    sessions_path = sessions_dir / "sessions.json"
    sessions_path.write_text(
        json.dumps(
            {
                "telegram:slash:312322764": {
                    "sessionId": "transport-session",
                },
                "agent:main:openai:transport-session": {
                    "sessionId": "transport-session",
                    "modelProvider": "lmstudio",
                    "model": "nvidia/nemotron-3-nano",
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = repair_sessions(
        sessions_path,
        channels=("telegram",),
        default_provider="lmstudio",
        default_model="lmstudio/nvidia/nemotron-3-nano",
    )

    assert report["changed"] is True
    assert report["reset_polluted_direct_sessions"] == 1
    assert report["reset_broken_transport_aliases"] == 1
    payload = json.loads(sessions_path.read_text(encoding="utf-8"))
    assert "telegram:slash:312322764" not in payload
    assert "agent:main:openai:transport-session" not in payload
    archived = list(sessions_dir.glob("transport-session.polluted_*.jsonl"))
    assert len(archived) == 1


def test_repair_sessions_resets_missing_channel_session_file(tmp_path: Path) -> None:
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    sessions_path = sessions_dir / "sessions.json"
    sessions_path.write_text(
        json.dumps(
            {
                "agent:main:telegram:group:-1001804661353": {
                    "sessionId": "missing",
                    "sessionFile": "missing.jsonl",
                    "modelProvider": "lmstudio",
                    "model": "nvidia/nemotron-3-nano",
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = repair_sessions(
        sessions_path,
        channels=("telegram",),
        default_provider="lmstudio",
        default_model="lmstudio/nvidia/nemotron-3-nano",
    )

    assert report["changed"] is True
    assert report["reset_missing_session_files"] == 1
    payload = json.loads(sessions_path.read_text(encoding="utf-8"))
    assert "agent:main:telegram:group:-1001804661353" not in payload


def test_repair_sessions_resets_auth_fallback_direct_session(tmp_path: Path) -> None:
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    session_file = sessions_dir / "auth-fallback.jsonl"
    session_file.write_text(
        '{"role":"assistant","content":"401 Malformed LM Studio API token provided: local-dumm*****"}\n'
        '{"fallbackNoticeReason":"auth","fallbackNoticeActiveModel":"google/gemini-2.5-flash"}\n',
        encoding="utf-8",
    )
    sessions_path = sessions_dir / "sessions.json"
    sessions_path.write_text(
        json.dumps(
            {
                "agent:main:imessage:direct:pavelr7@me.com": {
                    "sessionId": "auth",
                    "sessionFile": "auth-fallback.jsonl",
                    "modelProvider": "google",
                    "model": "gemini-2.5-flash",
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = repair_sessions(
        sessions_path,
        channels=("imessage",),
        default_provider="lmstudio",
        default_model="lmstudio/nvidia/nemotron-3-nano",
    )

    assert report["changed"] is True
    assert report["reset_auth_fallback_sessions"] == 1
    payload = json.loads(sessions_path.read_text(encoding="utf-8"))
    assert "agent:main:imessage:direct:pavelr7@me.com" not in payload
    archived = list(sessions_dir.glob("auth-fallback.authfallback_*.jsonl"))
    assert len(archived) == 1


def test_repair_sessions_resets_legacy_openai_bootstrap_transcript(tmp_path: Path) -> None:
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    session_file = sessions_dir / "legacy-openai.jsonl"
    session_file.write_text(
        '{"type":"session","cwd":"/Users/pablito/.openclaw/workspace"}\n'
        '{"role":"assistant","content":"<available_skills> apple-notes gh-issues coding-agent </available_skills>"}\n',
        encoding="utf-8",
    )
    sessions_path = sessions_dir / "sessions.json"
    sessions_path.write_text(
        json.dumps(
            {
                "agent:main:openai:legacy-openai": {
                    "sessionId": "legacy-openai",
                    "sessionFile": "legacy-openai.jsonl",
                    "modelProvider": "lmstudio",
                    "model": "nvidia/nemotron-3-nano",
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = repair_sessions(
        sessions_path,
        channels=("telegram",),
        default_provider="lmstudio",
        default_model="lmstudio/nvidia/nemotron-3-nano",
    )

    assert report["changed"] is True
    assert report["reset_legacy_bootstrap_transcript_sessions"] == 1
    payload = json.loads(sessions_path.read_text(encoding="utf-8"))
    assert "agent:main:openai:legacy-openai" not in payload
    archived = list(sessions_dir.glob("legacy-openai.legacybootstrap_*.jsonl"))
    assert len(archived) == 1


def test_repair_sessions_resets_direct_session_from_legacy_workspace(tmp_path: Path) -> None:
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    session_file = sessions_dir / "legacy-workspace.jsonl"
    session_file.write_text(
        '\n'.join(
            [
                '{"type":"session","version":3,"id":"legacy-workspace","cwd":"/Users/pablito/.openclaw/workspace"}',
                '{"role":"assistant","content":"ok"}',
            ]
        )
        + '\n',
        encoding="utf-8",
    )
    sessions_path = sessions_dir / "sessions.json"
    sessions_path.write_text(
        json.dumps(
            {
                "agent:main:whatsapp:direct:+123": {
                    "sessionId": "legacy-workspace",
                    "sessionFile": "legacy-workspace.jsonl",
                    "modelProvider": "lmstudio",
                    "model": "nvidia/nemotron-3-nano",
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = repair_sessions(
        sessions_path,
        channels=("whatsapp",),
        default_provider="lmstudio",
        default_model="lmstudio/nvidia/nemotron-3-nano",
    )

    assert report["changed"] is True
    assert report["reset_legacy_workspace_sessions"] == 1
    updated = json.loads(sessions_path.read_text(encoding="utf-8"))
    assert "agent:main:whatsapp:direct:+123" not in updated
    archived = list(sessions_dir.glob("legacy-workspace.legacyworkspace_*.jsonl"))
    assert len(archived) == 1


def test_repair_sessions_archives_orphaned_polluted_transcript(tmp_path: Path) -> None:
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    (sessions_dir / "orphan-polluted.jsonl").write_text(
        '{"role":"assistant","content":"[[reply_to_current]] Я здесь и готов помочь."}\n'
        '{"role":"toolCall","content":"<|begin_of_box|>tool [[reply_to_current]]"}\n',
        encoding="utf-8",
    )
    active_file = sessions_dir / "active.jsonl"
    active_file.write_text('{"role":"assistant","content":"ok"}\n', encoding="utf-8")
    sessions_path = sessions_dir / "sessions.json"
    sessions_path.write_text(
        json.dumps(
            {
                "agent:main:telegram:direct:312322764": {
                    "sessionId": "active",
                    "sessionFile": "active.jsonl",
                    "modelProvider": "lmstudio",
                    "model": "nvidia/nemotron-3-nano",
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = repair_sessions(
        sessions_path,
        channels=("telegram",),
        default_provider="lmstudio",
        default_model="lmstudio/nvidia/nemotron-3-nano",
    )

    assert report["changed"] is True
    assert report["fixed_entries"] == 0
    assert report["reset_polluted_orphan_session_files"] == 1
    archived = list(sessions_dir.glob("orphan-polluted.polluted_*.jsonl"))
    assert len(archived) == 1
    assert active_file.exists()


def test_repair_sessions_does_not_rearchive_already_archived_orphan(tmp_path: Path) -> None:
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    archived_existing = sessions_dir / "old.polluted_20260308_000000.jsonl"
    archived_existing.write_text('{"role":"assistant","content":"stale"}\n', encoding="utf-8")
    sessions_path = sessions_dir / "sessions.json"
    sessions_path.write_text("{}", encoding="utf-8")

    report = repair_sessions(
        sessions_path,
        channels=("telegram",),
        default_provider="lmstudio",
        default_model="lmstudio/nvidia/nemotron-3-nano",
    )

    assert report["changed"] is False
    assert report["reset_polluted_orphan_session_files"] == 0
    assert archived_existing.exists()


def test_repair_sessions_resets_main_session_from_legacy_workspace(tmp_path: Path) -> None:
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    session_file = sessions_dir / "legacy-main.jsonl"
    session_file.write_text(
        '\n'.join(
            [
                '{"type":"session","version":3,"id":"legacy-main","cwd":"/Users/pablito/.openclaw/workspace"}',
                '{"role":"assistant","content":"ok"}',
            ]
        )
        + '\n',
        encoding="utf-8",
    )
    sessions_path = sessions_dir / "sessions.json"
    sessions_path.write_text(
        json.dumps(
            {
                "agent:main:main": {
                    "sessionId": "legacy-main",
                    "sessionFile": "legacy-main.jsonl",
                    "modelProvider": "lmstudio",
                    "model": "nvidia/nemotron-3-nano",
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = repair_sessions(
        sessions_path,
        channels=("telegram", "whatsapp", "imessage"),
        default_provider="lmstudio",
        default_model="lmstudio/nvidia/nemotron-3-nano",
    )

    assert report["changed"] is True
    assert report["reset_legacy_workspace_sessions"] == 1
    updated = json.loads(sessions_path.read_text(encoding="utf-8"))
    assert "agent:main:main" not in updated
    archived = list(sessions_dir.glob("legacy-main.legacyworkspace_*.jsonl"))
    assert len(archived) == 1


def test_repair_sessions_resets_cron_session_from_legacy_workspace(tmp_path: Path) -> None:
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    session_file = sessions_dir / "legacy-cron.jsonl"
    session_file.write_text(
        '\n'.join(
            [
                '{"type":"session","version":3,"id":"legacy-cron","cwd":"/Users/pablito/.openclaw/workspace"}',
                '{"role":"assistant","content":"cron ok"}',
            ]
        )
        + '\n',
        encoding="utf-8",
    )
    sessions_path = sessions_dir / "sessions.json"
    sessions_path.write_text(
        json.dumps(
            {
                "agent:main:cron:announce:jokes": {
                    "sessionId": "legacy-cron",
                    "sessionFile": "legacy-cron.jsonl",
                    "modelProvider": "lmstudio",
                    "model": "nvidia/nemotron-3-nano",
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = repair_sessions(
        sessions_path,
        channels=("telegram", "whatsapp", "imessage"),
        default_provider="lmstudio",
        default_model="lmstudio/nvidia/nemotron-3-nano",
    )

    assert report["changed"] is True
    assert report["reset_legacy_workspace_sessions"] == 1
    updated = json.loads(sessions_path.read_text(encoding="utf-8"))
    assert "agent:main:cron:announce:jokes" not in updated
    archived = list(sessions_dir.glob("legacy-cron.legacyworkspace_*.jsonl"))
    assert len(archived) == 1


def test_repair_sessions_keeps_non_legacy_openai_transcript(tmp_path: Path) -> None:
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    session_file = sessions_dir / "fresh-openai.jsonl"
    session_file.write_text(
        '{"role":"system","content":"workspace \\"/Users/pablito/.openclaw/workspace-main-messaging\\""}\n'
        '{"role":"assistant","content":"На связи, transport ok."}\n',
        encoding="utf-8",
    )
    sessions_path = sessions_dir / "sessions.json"
    sessions_path.write_text(
        json.dumps(
            {
                "agent:main:openai:fresh-openai": {
                    "sessionId": "fresh-openai",
                    "sessionFile": "fresh-openai.jsonl",
                    "modelProvider": "lmstudio",
                    "model": "nvidia/nemotron-3-nano",
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = repair_sessions(
        sessions_path,
        channels=("telegram",),
        default_provider="lmstudio",
        default_model="lmstudio/nvidia/nemotron-3-nano",
    )

    assert report["reset_legacy_bootstrap_transcript_sessions"] == 0
    payload = json.loads(sessions_path.read_text(encoding="utf-8"))
    assert "agent:main:openai:fresh-openai" in payload
