"""Тесты генератора локального Xcode-проекта для iPhone companion."""

from __future__ import annotations

from pathlib import Path

from scripts.ios_companion_project_lib import (
    GenerationConfig,
    CompanionProjectError,
    default_bundle_id,
    default_project_dir,
    ensure_skeleton_ready,
    make_local_readme,
    make_project_spec,
    sanitize_bundle_fragment,
)


def test_sanitize_bundle_fragment_normalizes_noise() -> None:
    assert sanitize_bundle_fragment(" USER3 / MacBook-Pro ") == "user3.macbook.pro"


def test_default_bundle_id_uses_username_and_host() -> None:
    assert default_bundle_id(username="USER3", host="Krab-Mac") == "com.antigravity.krabvoice.user3.krab.mac"


def test_default_project_dir_is_per_account(tmp_path: Path) -> None:
    assert default_project_dir(home_dir=tmp_path, username="USER3") == tmp_path / "Projects" / "KrabVoiceiOS-user3"


def test_ensure_skeleton_ready_requires_all_swift_files(tmp_path: Path) -> None:
    (tmp_path / "KrabVoiceApp.swift").write_text("", encoding="utf-8")
    try:
        ensure_skeleton_ready(tmp_path)
    except CompanionProjectError as error:
        assert "ContentView.swift" in str(error)
    else:
        raise AssertionError("Ожидали ошибку при неполном skeleton")


def test_make_project_spec_contains_bundle_id_and_permissions(tmp_path: Path) -> None:
    skeleton_dir = tmp_path / "ios" / "KrabVoiceiOS"
    skeleton_dir.mkdir(parents=True)
    config = GenerationConfig(
        repo_root=tmp_path,
        voice_gateway_root=tmp_path / "voice",
        skeleton_dir=skeleton_dir,
        project_dir=tmp_path / "Projects" / "KrabVoiceiOS-user3",
        bundle_id="com.antigravity.krabvoice.user3.krab.mac",
        simulator_name="iPhone 17 Pro Max",
        open_xcode=False,
        run_simulator_build=True,
    )

    spec = make_project_spec(config)

    assert 'PRODUCT_BUNDLE_IDENTIFIER: "com.antigravity.krabvoice.user3.krab.mac"' in spec
    assert 'INFOPLIST_KEY_NSMicrophoneUsageDescription' in spec
    assert str(skeleton_dir.resolve()) in spec


def test_make_local_readme_mentions_personal_team(tmp_path: Path) -> None:
    config = GenerationConfig(
        repo_root=tmp_path,
        voice_gateway_root=tmp_path / "voice",
        skeleton_dir=tmp_path / "voice" / "ios" / "KrabVoiceiOS",
        project_dir=tmp_path / "Projects" / "KrabVoiceiOS-user3",
        bundle_id="com.antigravity.krabvoice.user3.krab.mac",
        simulator_name="iPhone 17 Pro Max",
        open_xcode=True,
        run_simulator_build=True,
    )

    readme = make_local_readme(config)

    assert "Personal Team" in readme
    assert "Health-check" in readme
