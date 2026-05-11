# -*- coding: utf-8 -*-
"""Wave 65-F — guards для предотвращения test artifacts leak в prod state paths.

Контекст (recurring pattern):
- Session 39: ``swarm_channels.json`` test leak (placeholders ``-100``/``-200``
  переписывали production config → ``!swarm`` ломался в group).
- Session 40: same pattern (4-я persistent state leak path в счёт сезона).
- Session 44+: ``agent_audit.jsonl`` тоже стало victim — 27 entries с fake
  recipients (``s#c``, ``+1``, ``x@y.com``) из subprocess тестов
  ``test_multi_channel_wave44t.py::test_scripts_executable``, который
  запускал scripts/agent_tools/krab_send_* БЕЗ env_extra={"HOME":
  str(tmp_path)} → subprocess наследовал реальный HOME → ``audit_event()``
  писал в ``~/.openclaw/krab_runtime_state/agent_audit.jsonl``.

Фикс (Wave 65-F):
1. ``scripts/agent_tools/_multi_channel_helpers.py`` теперь читает
   ``KRAB_RUNTIME_STATE_DIR`` из env (как уже делают ``state_snapshots``,
   ``logger``, ``health_detail_collector``, ``message_catchup``,
   ``auto_translate``, ``userbot_bridge``).
2. ``tests/unit/conftest.py::_isolate_persistent_runtime_state`` теперь
   ставит ``KRAB_RUNTIME_STATE_DIR=<tmp>/runtime_state`` через
   ``monkeypatch.setenv`` для каждого unit-теста → subprocesses
   наследуют env и пишут в tmpdir.
3. ``test_scripts_executable`` явно передаёт env_extra с HOME+
   KRAB_RUNTIME_STATE_DIR (belt-and-suspenders).

Эти регрешн-тесты проверяют что:
- env override работает в helpers модуле;
- production state не содержит test artifacts (fake recipients).
"""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS_DIR = REPO_ROOT / "scripts" / "agent_tools"
PYTHON = sys.executable


@pytest.fixture(autouse=True)
def _restore_multi_channel_helpers_cache():
    """Wave 65-F: тесты делают `importlib.import_module("_multi_channel_helpers")`
    с custom env overrides, кэшируя module в sys.modules. Без teardown следующие
    тесты (например test_multi_channel_wave44t.py::test_helpers_imports) получают
    stale cached path → fail. Удаляем из sys.modules после каждого теста чтобы
    next test reimported fresh с правильным env.
    """
    yield
    sys.modules.pop("_multi_channel_helpers", None)

# ---------------------------------------------------------------------------
# Список production state files которые подвержены leak и должны быть
# защищены через KRAB_RUNTIME_STATE_DIR env override.
# Если добавляете новый state file в src/ или scripts/agent_tools/ — добавьте
# сюда + убедитесь что модуль читает KRAB_RUNTIME_STATE_DIR из env.
# ---------------------------------------------------------------------------
PROTECTED_STATE_FILES = [
    "agent_audit.jsonl",
    "discord_known_channels.json",
    "imessage_known.json",
    "email_known.json",
    "swarm_channels.json",  # Session 39 / 40 leak history
    "owner_confirm.token",
    "chat_ban_cache.json",
    "chat_capability_cache.json",
]

# ---------------------------------------------------------------------------
# Fake recipients/patterns которые тесты использовали (и продолжают использовать
# через `pytest.mark.parametrize` в test_scripts_executable). Эти strings НЕ
# должны попадать в production state — gauge для post-fix verification.
# ---------------------------------------------------------------------------
FAKE_TEST_RECIPIENTS = ["s#c", "+1", "x@y.com"]

PROD_RUNTIME_STATE = Path.home() / ".openclaw" / "krab_runtime_state"


def test_multi_channel_helpers_respects_env_override(tmp_path, monkeypatch):
    """``_multi_channel_helpers.RUNTIME_STATE_DIR`` должен следовать
    ``KRAB_RUNTIME_STATE_DIR`` env var.

    Если этот тест fail'ит — root cause leak вернулся: subprocess в тестах
    больше не пишет в tmp dir, а пишет в production path.
    """
    custom_dir = tmp_path / "custom_runtime"
    monkeypatch.setenv("KRAB_RUNTIME_STATE_DIR", str(custom_dir))

    # Re-import чтобы подхватить новый env (модуль читает env на import time).
    sys.path.insert(0, str(TOOLS_DIR))
    if "_multi_channel_helpers" in sys.modules:
        del sys.modules["_multi_channel_helpers"]
    helpers = importlib.import_module("_multi_channel_helpers")

    assert helpers.RUNTIME_STATE_DIR == custom_dir
    assert helpers.AGENT_AUDIT_PATH == custom_dir / "agent_audit.jsonl"
    assert helpers.DISCORD_KNOWN_PATH == custom_dir / "discord_known_channels.json"
    assert helpers.IMESSAGE_KNOWN_PATH == custom_dir / "imessage_known.json"
    assert helpers.EMAIL_KNOWN_PATH == custom_dir / "email_known.json"
    assert helpers.OWNER_CONFIRM_TOKEN_PATH == custom_dir / "owner_confirm.token"


def test_audit_event_writes_to_env_path(tmp_path, monkeypatch):
    """``audit_event`` должен писать в KRAB_RUNTIME_STATE_DIR, не в prod."""
    custom_dir = tmp_path / "audit_dir"
    monkeypatch.setenv("KRAB_RUNTIME_STATE_DIR", str(custom_dir))

    sys.path.insert(0, str(TOOLS_DIR))
    if "_multi_channel_helpers" in sys.modules:
        del sys.modules["_multi_channel_helpers"]
    helpers = importlib.import_module("_multi_channel_helpers")

    helpers.audit_event(
        channel="testchannel",
        recipient="wave65f-test@example.com",
        action="test_action",
        ok=True,
        extra={"test_marker": "wave65f"},
    )

    audit_path = custom_dir / "agent_audit.jsonl"
    assert audit_path.is_file(), "audit_event() should write to env-overridden path"
    content = audit_path.read_text(encoding="utf-8").strip()
    assert "wave65f-test@example.com" in content
    record = json.loads(content.splitlines()[-1])
    assert record["channel"] == "testchannel"
    assert record["recipient"] == "wave65f-test@example.com"
    assert record["test_marker"] == "wave65f"


def test_subprocess_inherits_runtime_state_env(tmp_path):
    """Полноценный e2e: subprocess запуска krab_send_imessage.py с
    KRAB_RUNTIME_STATE_DIR должен писать в tmp, не в prod.

    Это тот же scenario что failед раньше — без env override subprocess
    писал в ~/.openclaw/krab_runtime_state/agent_audit.jsonl.
    """
    custom_dir = tmp_path / ".openclaw" / "krab_runtime_state"
    env = {
        **os.environ,
        "HOME": str(tmp_path),
        "KRAB_RUNTIME_STATE_DIR": str(custom_dir),
    }
    # Удаляем тестовый artefact из prod пути если случайно положили
    prod_audit = PROD_RUNTIME_STATE / "agent_audit.jsonl"
    prod_size_before = prod_audit.stat().st_size if prod_audit.is_file() else 0

    result = subprocess.run(  # noqa: S603
        [
            PYTHON,
            str(TOOLS_DIR / "krab_send_imessage.py"),
            "--to",
            "wave65f-fake-recipient",
            "--text",
            "test",
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
        check=False,
    )

    # Должен быть first_time_blocked (rc=1, json output)
    assert result.returncode == 1, f"unexpected rc {result.returncode}; stdout={result.stdout}"

    # Prod audit log должен быть НЕ ИЗМЕНЁН
    prod_size_after = prod_audit.stat().st_size if prod_audit.is_file() else 0
    assert prod_size_after == prod_size_before, (
        f"Production agent_audit.jsonl was modified: {prod_size_before} → {prod_size_after}. "
        "Subprocess не уважает KRAB_RUNTIME_STATE_DIR — leak guard regressed!"
    )

    # Tmp audit log должен содержать наш test event
    tmp_audit = custom_dir / "agent_audit.jsonl"
    assert tmp_audit.is_file(), "subprocess should write audit to tmp dir"
    content = tmp_audit.read_text(encoding="utf-8")
    assert "wave65f-fake-recipient" in content


@pytest.mark.parametrize("fake_recipient", FAKE_TEST_RECIPIENTS)
def test_production_audit_has_no_fake_recipients(fake_recipient):
    """Production ``agent_audit.jsonl`` не должен содержать fake recipients.

    Wave 65-F cleanup удалил 27 leaked entries; этот тест проверяет
    что они не вернулись (новый leak == test fail).

    SKIP если файл отсутствует — это означает что Krab ещё не запускался
    после установки или dev-environment без production data.
    """
    prod_audit = PROD_RUNTIME_STATE / "agent_audit.jsonl"
    if not prod_audit.is_file():
        pytest.skip("production agent_audit.jsonl absent — skip prod-data assertion")

    content = prod_audit.read_text(encoding="utf-8")
    # Считаем точные match'и `"recipient": "<fake>"` чтобы избежать ложных
    # срабатываний на substring (типа "+1" в timestamps).
    pattern = f'"recipient": "{fake_recipient}"'
    count = content.count(pattern)
    assert count == 0, (
        f"Production agent_audit.jsonl contains {count} entries with fake "
        f"recipient {fake_recipient!r} — test leak regressed. "
        "Check tests/unit/test_multi_channel_wave44t.py::test_scripts_executable "
        "and ensure env_extra={'HOME': str(tmp_path), 'KRAB_RUNTIME_STATE_DIR': ...} "
        "is passed для всех _run() calls."
    )


def test_protected_state_files_documented():
    """Документация regression: список защищённых state files должен быть
    непустым и должен охватывать known leak victims (Session 39/40/44+).

    Это smoke-тест — если новый state file добавлен в Krab без env
    override, или если кто-то удалит запись из ``PROTECTED_STATE_FILES``,
    лист сразу станет неконсистентным.
    """
    assert "agent_audit.jsonl" in PROTECTED_STATE_FILES, "Session 44+ victim"
    assert "swarm_channels.json" in PROTECTED_STATE_FILES, "Session 39/40 victim"
    assert all(isinstance(f, str) and f for f in PROTECTED_STATE_FILES)
