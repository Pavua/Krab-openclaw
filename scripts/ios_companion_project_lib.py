"""Генерация пер-учётка Xcode-проекта для iPhone companion.

Этот модуль нужен, чтобы shared-репозиторий оставался общим для всех учёток,
а Xcode-проект, signing-метаданные и локальные user-specific файлы жили отдельно
в домашней директории текущего пользователя.

Связь с проектом:
- читает SwiftUI skeleton из соседнего `Krab Voice Gateway/ios/KrabVoiceiOS`;
- создаёт локальный XcodeGen spec и Xcode project для free signing;
- помогает быстро открыть проект в Xcode и прогнать simulator-build без ручной рутины.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import getpass
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import textwrap
from typing import Iterable


APP_NAME = "KrabVoice"
DEPLOYMENT_TARGET = "16.0"
DEFAULT_APP_VERSION = "0.2.0"
REQUIRED_SWIFT_FILES = (
    "KrabVoiceApp.swift",
    "ContentView.swift",
    "GatewayClient.swift",
    "GatewayStreamClient.swift",
    "CallManager.swift",
    "PushRegistryManager.swift",
    "Models.swift",
)


@dataclass(frozen=True)
class GenerationConfig:
    """Конфигурация генерации локального iPhone companion проекта."""

    repo_root: Path
    voice_gateway_root: Path
    skeleton_dir: Path
    project_dir: Path
    bundle_id: str
    simulator_name: str
    open_xcode: bool
    run_simulator_build: bool
    app_name: str = APP_NAME
    deployment_target: str = DEPLOYMENT_TARGET
    app_version: str = DEFAULT_APP_VERSION


@dataclass(frozen=True)
class GenerationResult:
    """Итог генерации и локальной проверки Xcode-проекта."""

    project_dir: str
    xcodeproj_path: str
    project_spec_path: str
    readme_path: str
    bundle_id: str
    simulator_name: str
    simulator_build_ran: bool
    simulator_build_ok: bool
    opened_in_xcode: bool
    generated_at_utc: str

    def to_dict(self) -> dict[str, object]:
        """Возвращает JSON-совместимый словарь для ops-артефактов."""

        return asdict(self)


class CompanionProjectError(RuntimeError):
    """Ошибка генерации Xcode companion проекта."""


def sanitize_bundle_fragment(raw_value: str) -> str:
    """Приводит произвольную строку к безопасному bundle-id фрагменту."""

    lowered = raw_value.strip().lower()
    replaced = re.sub(r"[^a-z0-9]+", ".", lowered)
    collapsed = re.sub(r"\.+", ".", replaced).strip(".")
    return collapsed or "user"


def default_bundle_id(username: str | None = None, host: str | None = None) -> str:
    """Собирает достаточно уникальный bundle identifier для free signing."""

    account = sanitize_bundle_fragment(username or getpass.getuser())
    machine = sanitize_bundle_fragment(host or platform.node() or "mac")
    return f"com.antigravity.krabvoice.{account}.{machine}"


def default_project_dir(home_dir: Path | None = None, username: str | None = None) -> Path:
    """Возвращает локальную директорию per-account Xcode-проекта."""

    user_home = (home_dir or Path.home()).expanduser()
    account = sanitize_bundle_fragment(username or getpass.getuser())
    return user_home / "Projects" / f"KrabVoiceiOS-{account}"


def ensure_skeleton_ready(skeleton_dir: Path, required_files: Iterable[str] = REQUIRED_SWIFT_FILES) -> None:
    """Проверяет, что SwiftUI skeleton целиком доступен перед генерацией проекта."""

    missing = [name for name in required_files if not (skeleton_dir / name).is_file()]
    if missing:
        joined = ", ".join(sorted(missing))
        raise CompanionProjectError(
            f"iPhone skeleton неполный: отсутствуют файлы {joined} в {skeleton_dir}"
        )


def _yaml_quote(value: str) -> str:
    """Экранирует значение для безопасной записи в YAML spec."""

    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def make_project_spec(config: GenerationConfig) -> str:
    """Генерирует XcodeGen spec для локального iOS App target."""

    skeleton_path = str(config.skeleton_dir.resolve())
    return textwrap.dedent(
        f"""
        name: {config.app_name}
        options:
          deploymentTarget:
            iOS: {config.deployment_target}
          createIntermediateGroups: true
          groupSortPosition: top
        settings:
          base:
            SWIFT_VERSION: "5.0"
            MARKETING_VERSION: {config.app_version}
            CURRENT_PROJECT_VERSION: 1
            TARGETED_DEVICE_FAMILY: "1"
            SUPPORTS_MACCATALYST: NO
            CODE_SIGN_STYLE: Automatic
            DEVELOPMENT_ASSET_PATHS: ""
        targets:
          {config.app_name}:
            type: application
            platform: iOS
            deploymentTarget: {config.deployment_target}
            sources:
              - path: {_yaml_quote(skeleton_path)}
                excludes:
                  - README.md
            settings:
              base:
                PRODUCT_NAME: {_yaml_quote(config.app_name)}
                PRODUCT_BUNDLE_IDENTIFIER: {_yaml_quote(config.bundle_id)}
                GENERATE_INFOPLIST_FILE: YES
                INFOPLIST_KEY_CFBundleDisplayName: {_yaml_quote(config.app_name)}
                INFOPLIST_KEY_LSRequiresIPhoneOS: YES
                INFOPLIST_KEY_NSMicrophoneUsageDescription: {_yaml_quote('Нужен доступ к микрофону для перевода звонка.')}
                INFOPLIST_KEY_NSLocalNetworkUsageDescription: {_yaml_quote('Нужен доступ к локальной сети для подключения к Krab Gateway.')}
                INFOPLIST_KEY_UIApplicationSupportsIndirectInputEvents: YES
            scheme:
              gatherCoverageData: false
              testTargets: []
        """
    ).strip() + "\n"


def make_local_readme(config: GenerationConfig) -> str:
    """Создаёт локальную памятку рядом с generated Xcode project."""

    return textwrap.dedent(
        f"""
        # KrabVoice iOS: локальный Xcode bootstrap

        Этот каталог создан автоматически для учётки `{getpass.getuser()}`.
        Репозиторий остаётся общим, а Xcode project и signing-метаданные живут локально.

        ## Что уже сделано

        - Сгенерирован проект `{config.app_name}.xcodeproj` через `xcodegen`.
        - Подключён SwiftUI skeleton из `{config.skeleton_dir}`.
        - Прописаны `NSMicrophoneUsageDescription` и `NSLocalNetworkUsageDescription`.
        - Bundle ID по умолчанию: `{config.bundle_id}`.

        ## Что осталось в Xcode

        1. Открыть `{config.app_name}.xcodeproj`.
        2. В target -> Signing & Capabilities включить `Automatically manage signing`.
        3. Выбрать `Team = Personal Team` для текущего Apple ID.
        4. Подключить реальный iPhone и выбрать его как Run Destination.
        5. На iPhone при необходимости подтвердить `Trust Developer`.

        ## Настройки в приложении после первого запуска

        - `Gateway URL`: заменить `http://127.0.0.1:8090` на IP вашего Mac, например `http://192.168.x.x:8090`.
        - `Gateway API key`: оставить пустым, если `KRAB_VOICE_API_KEY` не задан.
        - Затем нажать `Health-check`, потом `Старт`.

        ## Быстрая sanity-проверка

        По умолчанию генератор прогоняет simulator-build на `{config.simulator_name}` без code signing.
        Это доказывает, что project собирается до этапа реального free signing.
        """
    ).strip() + "\n"


def write_project_files(config: GenerationConfig) -> tuple[Path, Path]:
    """Записывает XcodeGen spec и локальную README в директорию проекта."""

    config.project_dir.mkdir(parents=True, exist_ok=True)
    spec_path = config.project_dir / "project.yml"
    readme_path = config.project_dir / "README_RU.md"
    spec_path.write_text(make_project_spec(config), encoding="utf-8")
    readme_path.write_text(make_local_readme(config), encoding="utf-8")
    return spec_path, readme_path


def run_command(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """Запускает системную команду и бросает понятную ошибку при неуспехе."""

    completed = subprocess.run(
        command,
        cwd=str(cwd),
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.strip()
        stdout = completed.stdout.strip()
        details = stderr or stdout or f"код возврата {completed.returncode}"
        raise CompanionProjectError(f"Команда {' '.join(command)} завершилась ошибкой: {details}")
    return completed


def generate_xcode_project(config: GenerationConfig, spec_path: Path) -> Path:
    """Генерирует `.xcodeproj` через установленный `xcodegen`."""

    run_command(["xcodegen", "generate", "--spec", str(spec_path)], cwd=config.project_dir)
    xcodeproj_path = config.project_dir / f"{config.app_name}.xcodeproj"
    if not xcodeproj_path.exists():
        raise CompanionProjectError(f"Xcode project не был создан: {xcodeproj_path}")
    return xcodeproj_path


def build_for_simulator(xcodeproj_path: Path, scheme: str, simulator_name: str, project_dir: Path) -> None:
    """Прогоняет simulator build без code signing, чтобы проверить компиляцию."""

    run_command(
        [
            "xcodebuild",
            "-project",
            str(xcodeproj_path),
            "-scheme",
            scheme,
            "-destination",
            f"platform=iOS Simulator,name={simulator_name}",
            "CODE_SIGNING_ALLOWED=NO",
            "build",
        ],
        cwd=project_dir,
    )


def open_in_xcode(xcodeproj_path: Path) -> None:
    """Открывает сгенерированный проект в Xcode."""

    run_command(["open", str(xcodeproj_path)], cwd=xcodeproj_path.parent)


def write_ops_artifact(repo_root: Path, result: GenerationResult) -> Path:
    """Сохраняет свежий ops-артефакт о генерации Xcode companion проекта."""

    ops_dir = repo_root / "artifacts" / "ops"
    ops_dir.mkdir(parents=True, exist_ok=True)
    account = sanitize_bundle_fragment(getpass.getuser())
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")
    latest_path = ops_dir / f"iphone_companion_xcode_project_{account}_latest.json"
    stamped_path = ops_dir / f"iphone_companion_xcode_project_{account}_{timestamp}.json"
    payload = json.dumps(result.to_dict(), ensure_ascii=False, indent=2) + "\n"
    latest_path.write_text(payload, encoding="utf-8")
    stamped_path.write_text(payload, encoding="utf-8")
    return latest_path


def build_generation_result(
    *,
    config: GenerationConfig,
    xcodeproj_path: Path,
    spec_path: Path,
    readme_path: Path,
    simulator_build_ok: bool,
) -> GenerationResult:
    """Собирает единый итог генерации для логов и артефактов."""

    return GenerationResult(
        project_dir=str(config.project_dir),
        xcodeproj_path=str(xcodeproj_path),
        project_spec_path=str(spec_path),
        readme_path=str(readme_path),
        bundle_id=config.bundle_id,
        simulator_name=config.simulator_name,
        simulator_build_ran=config.run_simulator_build,
        simulator_build_ok=simulator_build_ok,
        opened_in_xcode=config.open_xcode,
        generated_at_utc=datetime.now(timezone.utc).isoformat(),
    )


def load_default_config(
    repo_root: Path,
    *,
    project_dir: Path | None = None,
    bundle_id: str | None = None,
    simulator_name: str | None = None,
    open_xcode: bool = False,
    run_simulator_build: bool = True,
) -> GenerationConfig:
    """Собирает конфиг генератора из стандартных путей текущей учётки."""

    voice_gateway_root = Path(
        os.environ.get(
            "KRAB_VOICE_GATEWAY_DIR",
            str((repo_root / ".." / "Krab Voice Gateway").resolve()),
        )
    ).expanduser()
    skeleton_dir = voice_gateway_root / "ios" / "KrabVoiceiOS"
    return GenerationConfig(
        repo_root=repo_root,
        voice_gateway_root=voice_gateway_root,
        skeleton_dir=skeleton_dir,
        project_dir=(project_dir or default_project_dir()).expanduser(),
        bundle_id=bundle_id or default_bundle_id(),
        simulator_name=simulator_name or os.environ.get("KRAB_IOS_SIMULATOR_NAME", "iPhone 17 Pro Max"),
        open_xcode=open_xcode,
        run_simulator_build=run_simulator_build,
    )
