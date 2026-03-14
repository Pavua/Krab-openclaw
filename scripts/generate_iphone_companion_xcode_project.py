#!/usr/bin/env python3
"""Генерирует локальный Xcode-проект для iPhone companion и проверяет сборку.

Скрипт создаёт per-account Xcode project вне shared-репозитория, чтобы не смешивать
общий код и user-specific signing-настройки. После генерации он может открыть проект
в Xcode и прогнать simulator-build без code signing.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.ios_companion_project_lib import (  # noqa: E402
    CompanionProjectError,
    build_for_simulator,
    build_generation_result,
    ensure_skeleton_ready,
    generate_xcode_project,
    load_default_config,
    open_in_xcode,
    write_ops_artifact,
    write_project_files,
)


def parse_args() -> argparse.Namespace:
    """Разбирает аргументы CLI без лишней магии."""

    parser = argparse.ArgumentParser(
        description="Подготовить локальный Xcode-проект для KrabVoice iPhone companion."
    )
    parser.add_argument(
        "--project-dir",
        type=Path,
        help="Куда создать локальный Xcode project. По умолчанию ~/Projects/KrabVoiceiOS-<user>",
    )
    parser.add_argument(
        "--bundle-id",
        type=str,
        help="Явный bundle identifier. По умолчанию генерируется из user+host.",
    )
    parser.add_argument(
        "--simulator-name",
        type=str,
        help="Имя iOS Simulator для build smoke. По умолчанию iPhone 17 Pro Max.",
    )
    parser.add_argument(
        "--open-xcode",
        action="store_true",
        help="Открыть сгенерированный .xcodeproj в Xcode после генерации.",
    )
    parser.add_argument(
        "--skip-simulator-build",
        action="store_true",
        help="Не запускать simulator-build после генерации проекта.",
    )
    return parser.parse_args()


def main() -> int:
    """Основной сценарий генерации companion Xcode project."""

    args = parse_args()
    config = load_default_config(
        REPO_ROOT,
        project_dir=args.project_dir,
        bundle_id=args.bundle_id,
        simulator_name=args.simulator_name,
        open_xcode=args.open_xcode,
        run_simulator_build=not args.skip_simulator_build,
    )

    try:
        ensure_skeleton_ready(config.skeleton_dir)
        spec_path, readme_path = write_project_files(config)
        xcodeproj_path = generate_xcode_project(config, spec_path)

        simulator_build_ok = False
        if config.run_simulator_build:
            build_for_simulator(
                xcodeproj_path=xcodeproj_path,
                scheme=config.app_name,
                simulator_name=config.simulator_name,
                project_dir=config.project_dir,
            )
            simulator_build_ok = True

        if config.open_xcode:
            open_in_xcode(xcodeproj_path)

        result = build_generation_result(
            config=config,
            xcodeproj_path=xcodeproj_path,
            spec_path=spec_path,
            readme_path=readme_path,
            simulator_build_ok=simulator_build_ok,
        )
        artifact_path = write_ops_artifact(REPO_ROOT, result)
        output = result.to_dict()
        output["ops_artifact"] = str(artifact_path)
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    except CompanionProjectError as error:
        print(f"❌ {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
