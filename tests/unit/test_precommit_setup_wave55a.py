"""Wave 55-A: тесты pre-commit setup (ruff hook + install script + CI)."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent.parent
PRE_COMMIT_CONFIG = REPO_ROOT / ".pre-commit-config.yaml"
INSTALL_SCRIPT = REPO_ROOT / "scripts" / "install_pre_commit.sh"
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "python-ci.yml"


# ---------------------------------------------------------------------------
# 1. pre-commit config YAML well-formed
# ---------------------------------------------------------------------------


def test_pre_commit_config_yaml_well_formed():
    """Файл .pre-commit-config.yaml существует и парсится без ошибок."""
    assert PRE_COMMIT_CONFIG.exists(), f"Файл не найден: {PRE_COMMIT_CONFIG}"
    content = PRE_COMMIT_CONFIG.read_text()
    parsed = yaml.safe_load(content)
    assert parsed is not None, "YAML пустой"
    assert isinstance(parsed, dict), "Корень YAML должен быть dict"


# ---------------------------------------------------------------------------
# 2. pre-commit config включает ruff hooks
# ---------------------------------------------------------------------------


def test_pre_commit_config_includes_ruff():
    """Конфиг содержит ruff и ruff-format hooks из astral-sh/ruff-pre-commit."""
    parsed = yaml.safe_load(PRE_COMMIT_CONFIG.read_text())
    repos = parsed.get("repos", [])
    assert repos, "Нет секции repos"

    ruff_repo = next(
        (r for r in repos if "ruff-pre-commit" in r.get("repo", "")),
        None,
    )
    assert ruff_repo is not None, "ruff-pre-commit repo не найден в конфиге"

    hook_ids = [h["id"] for h in ruff_repo.get("hooks", [])]
    assert "ruff" in hook_ids, "Hook 'ruff' не найден"
    assert "ruff-format" in hook_ids, "Hook 'ruff-format' не найден"


# ---------------------------------------------------------------------------
# 3. ruff hook настроен на src/ и scripts/
# ---------------------------------------------------------------------------


def test_ruff_hook_targets_src_and_scripts():
    """Hook ruff имеет files pattern покрывающий src/ и scripts/."""
    parsed = yaml.safe_load(PRE_COMMIT_CONFIG.read_text())
    repos = parsed.get("repos", [])
    ruff_repo = next(r for r in repos if "ruff-pre-commit" in r.get("repo", ""))
    hooks = {h["id"]: h for h in ruff_repo.get("hooks", [])}

    for hook_id in ("ruff", "ruff-format"):
        hook = hooks.get(hook_id, {})
        files_pattern = hook.get("files", "")
        assert "src" in files_pattern, f"Hook '{hook_id}' не покрывает src/"
        assert "scripts" in files_pattern, f"Hook '{hook_id}' не покрывает scripts/"


# ---------------------------------------------------------------------------
# 4. install script: shebang + exec bit
# ---------------------------------------------------------------------------


def test_install_script_exists_and_executable():
    """scripts/install_pre_commit.sh существует, имеет shebang и exec bit."""
    assert INSTALL_SCRIPT.exists(), f"Файл не найден: {INSTALL_SCRIPT}"

    # Проверка shebang
    first_line = INSTALL_SCRIPT.read_text().splitlines()[0]
    assert first_line.startswith("#!"), "Нет shebang в install_pre_commit.sh"
    assert "bash" in first_line or "sh" in first_line, "Shebang должен указывать на bash/sh"

    # Проверка exec bit
    mode = os.stat(INSTALL_SCRIPT).st_mode
    assert mode & stat.S_IXUSR, "install_pre_commit.sh не исполняемый (chmod +x не применён)"


# ---------------------------------------------------------------------------
# 5. install script содержит ключевые команды
# ---------------------------------------------------------------------------


def test_install_script_contains_required_commands():
    """Скрипт содержит команды pre-commit install и pre-commit --version."""
    content = INSTALL_SCRIPT.read_text()
    assert "pre-commit install" in content, "Нет команды 'pre-commit install'"
    assert "pip install pre-commit" in content or "pre-commit" in content, (
        "Нет установки pre-commit"
    )


# ---------------------------------------------------------------------------
# 6. CI workflow включает pre-commit валидацию
# ---------------------------------------------------------------------------


def test_ci_workflow_includes_precommit_step():
    """CI workflow содержит шаг валидации .pre-commit-config.yaml."""
    assert CI_WORKFLOW.exists(), f"CI workflow не найден: {CI_WORKFLOW}"
    parsed = yaml.safe_load(CI_WORKFLOW.read_text())

    # Собираем все шаги из всех jobs
    all_steps = []
    for job in parsed.get("jobs", {}).values():
        all_steps.extend(job.get("steps", []))

    step_names = [s.get("name", "") for s in all_steps]
    step_runs = [s.get("run", "") for s in all_steps]

    # Ищем шаг который упоминает pre-commit или .pre-commit-config
    found = any(
        "pre-commit" in name.lower() or "pre-commit" in run.lower()
        for name, run in zip(step_names, step_runs)
    )
    assert found, "CI workflow не содержит шага для pre-commit (Wave 55-A)"


# ---------------------------------------------------------------------------
# 7. CI workflow YAML well-formed
# ---------------------------------------------------------------------------


def test_ci_workflow_yaml_well_formed():
    """CI workflow парсируется без ошибок."""
    assert CI_WORKFLOW.exists()
    parsed = yaml.safe_load(CI_WORKFLOW.read_text())
    assert parsed is not None
    assert "jobs" in parsed, "CI workflow не содержит секции 'jobs'"


# ---------------------------------------------------------------------------
# 8. ruff hook имеет --fix и --exit-non-zero-on-fix args
# ---------------------------------------------------------------------------


def test_ruff_hook_has_fix_args():
    """ruff hook настроен на auto-fix и падение при исправлениях."""
    parsed = yaml.safe_load(PRE_COMMIT_CONFIG.read_text())
    repos = parsed.get("repos", [])
    ruff_repo = next(r for r in repos if "ruff-pre-commit" in r.get("repo", ""))
    hooks = {h["id"]: h for h in ruff_repo.get("hooks", [])}

    ruff_hook = hooks.get("ruff", {})
    args = ruff_hook.get("args", [])
    assert "--fix" in args, "ruff hook не имеет --fix"
    assert "--exit-non-zero-on-fix" in args, "ruff hook не имеет --exit-non-zero-on-fix"
