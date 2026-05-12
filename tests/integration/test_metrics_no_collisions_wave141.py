"""Wave 141: integration-тесты для metrics-collision audit.

Сканируем src/core/metrics/*.py через AST-парсер из
scripts/krab_metrics_collision_check.py, проверяем что:
- нет дублирующихся метрик между submodules
- audit JSON-отчёт корректен по структуре
- скрипт корректно ловит синтетические коллизии (negative case)
"""

from __future__ import annotations

import importlib.util
import json
import sys
import textwrap
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "krab_metrics_collision_check.py"


def _load_module():
    """Загружает scripts/krab_metrics_collision_check.py как модуль."""
    spec = importlib.util.spec_from_file_location(
        "_krab_metrics_collision_check_wave141", _SCRIPT
    )
    assert spec and spec.loader, "spec для script не создан"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def audit_module():
    return _load_module()


@pytest.fixture(scope="module")
def live_report(audit_module):
    pkg = _REPO_ROOT / "src" / "core" / "metrics"
    return audit_module.audit_metrics_package(pkg)


def test_metrics_package_exists():
    pkg = _REPO_ROOT / "src" / "core" / "metrics"
    assert pkg.is_dir(), "src/core/metrics/ должен существовать"
    assert any(pkg.glob("*.py")), "metrics package пуст"


def test_no_collisions_in_live_package(live_report):
    """Главный инвариант: в реальном пакете 0 коллизий."""
    assert live_report["collisions_count"] == 0, (
        f"Найдены коллизии метрик: {live_report['collisions']}"
    )
    assert live_report["total_metrics"] > 0, "Не нашли ни одной метрики — парсер сломан"


def test_report_schema_is_well_formed(live_report):
    """Проверяем что отчёт имеет ожидаемые поля и типы."""
    assert live_report["wave"] == 141
    assert isinstance(live_report["files_scanned"], list)
    assert isinstance(live_report["all_metrics"], dict)
    assert live_report["total_definitions"] >= live_report["total_metrics"]
    for name, submodules in live_report["all_metrics"].items():
        assert name.startswith("krab_"), f"метрика {name!r} не имеет префикса krab_"
        assert isinstance(submodules, list) and submodules


def test_collision_detected_on_synthetic_fixture(tmp_path, audit_module):
    """Negative case: подсовываем два файла с одинаковым именем метрики."""
    pkg = tmp_path / "metrics"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    common = textwrap.dedent(
        """
        from prometheus_client import Counter as _C
        m = _C("krab_wave141_synthetic_total", "synthetic")
        """
    ).strip()
    (pkg / "alpha.py").write_text(common, encoding="utf-8")
    (pkg / "beta.py").write_text(common, encoding="utf-8")

    report = audit_module.audit_metrics_package(pkg)
    assert report["collisions_count"] == 1
    assert "krab_wave141_synthetic_total" in report["collisions"]
    locs = report["collisions"]["krab_wave141_synthetic_total"]
    submodules = {loc["submodule"] for loc in locs}
    assert submodules == {"alpha", "beta"}


def test_write_report_creates_parents(tmp_path, audit_module):
    """write_report должен создавать parent directories."""
    out = tmp_path / "deep" / "nested" / "report.json"
    audit_module.write_report({"wave": 141, "collisions_count": 0}, out)
    assert out.is_file()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["wave"] == 141
