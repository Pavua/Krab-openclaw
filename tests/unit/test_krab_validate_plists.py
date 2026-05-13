"""Wave 209: tests for scripts/krab_validate_plists.py"""

from __future__ import annotations

import json
import plistlib
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import krab_validate_plists as kvp  # noqa: E402


def _write_plist(path: Path, data: dict) -> None:
    with path.open("wb") as f:
        plistlib.dump(data, f)


def _good_plist(tmp_path: Path, label: str = "ai.krab.demo") -> dict:
    interp = tmp_path / "python"
    if not interp.exists():
        interp.write_text("#!/bin/sh\necho hi\n")
        interp.chmod(0o755)
    script = tmp_path / "script.py"
    if not script.exists():
        script.write_text("print('hi')\n")
    wd = tmp_path / "work"
    wd.mkdir(exist_ok=True)
    return {
        "Label": label,
        "ProgramArguments": [str(interp), str(script)],
        "WorkingDirectory": str(wd),
        "StandardOutPath": str(tmp_path / "out.log"),
        "StandardErrorPath": str(tmp_path / "err.log"),
        "StartInterval": 300,
        "EnvironmentVariables": {"FOO": "bar"},
    }


def test_valid_plist_passes(tmp_path):
    pl = tmp_path / "ai.krab.demo.plist"
    _write_plist(pl, _good_plist(tmp_path))
    r = kvp.validate_plist(pl)
    assert r.ok, r.errors
    assert not r.errors
    assert r.label == "ai.krab.demo"


def test_label_mismatch_filename(tmp_path):
    pl = tmp_path / "ai.krab.demo.plist"
    data = _good_plist(tmp_path)
    data["Label"] = "ai.krab.wrong"
    _write_plist(pl, data)
    r = kvp.validate_plist(pl)
    assert not r.ok
    assert any("does not match filename" in e for e in r.errors)


def test_missing_label(tmp_path):
    pl = tmp_path / "ai.krab.demo.plist"
    data = _good_plist(tmp_path)
    del data["Label"]
    _write_plist(pl, data)
    r = kvp.validate_plist(pl)
    assert not r.ok
    assert any("Label" in e for e in r.errors)


def test_missing_interpreter(tmp_path):
    pl = tmp_path / "ai.krab.demo.plist"
    data = _good_plist(tmp_path)
    data["ProgramArguments"][0] = "/nonexistent/python"
    _write_plist(pl, data)
    r = kvp.validate_plist(pl)
    assert not r.ok
    assert any("interpreter not found" in e for e in r.errors)


def test_missing_script_path(tmp_path):
    pl = tmp_path / "ai.krab.demo.plist"
    data = _good_plist(tmp_path)
    data["ProgramArguments"].append("/nonexistent/whatever.py")
    _write_plist(pl, data)
    r = kvp.validate_plist(pl)
    assert not r.ok
    assert any("path not found" in e for e in r.errors)


def test_no_trigger_fails(tmp_path):
    pl = tmp_path / "ai.krab.demo.plist"
    data = _good_plist(tmp_path)
    del data["StartInterval"]
    _write_plist(pl, data)
    r = kvp.validate_plist(pl)
    assert not r.ok
    assert any("no trigger" in e for e in r.errors)


def test_run_at_load_counts_as_trigger(tmp_path):
    pl = tmp_path / "ai.krab.demo.plist"
    data = _good_plist(tmp_path)
    del data["StartInterval"]
    data["RunAtLoad"] = True
    data["KeepAlive"] = True
    _write_plist(pl, data)
    r = kvp.validate_plist(pl)
    assert r.ok, r.errors


def test_missing_working_directory(tmp_path):
    pl = tmp_path / "ai.krab.demo.plist"
    data = _good_plist(tmp_path)
    data["WorkingDirectory"] = "/nonexistent/dir"
    _write_plist(pl, data)
    r = kvp.validate_plist(pl)
    assert not r.ok
    assert any("WorkingDirectory" in e for e in r.errors)


def test_stdout_parent_warn_then_fix(tmp_path):
    pl = tmp_path / "ai.krab.demo.plist"
    data = _good_plist(tmp_path)
    missing = tmp_path / "missing_dir" / "out.log"
    data["StandardOutPath"] = str(missing)
    _write_plist(pl, data)

    r = kvp.validate_plist(pl, fix_soft=False)
    assert any("parent dir missing" in w for w in r.warnings)

    r2 = kvp.validate_plist(pl, fix_soft=True)
    assert any("created parent dir" in f for f in r2.fixed)
    assert missing.parent.exists()


def test_invalid_env_vars(tmp_path):
    pl = tmp_path / "ai.krab.demo.plist"
    data = _good_plist(tmp_path)
    data["EnvironmentVariables"] = {"FOO": 123}  # not a string
    _write_plist(pl, data)
    r = kvp.validate_plist(pl)
    assert not r.ok
    assert any("EnvironmentVariables" in e for e in r.errors)


def test_duplicate_labels_detection(tmp_path):
    p1 = tmp_path / "ai.krab.foo.plist"
    p2 = tmp_path / "ai.krab.bar.plist"
    d1 = _good_plist(tmp_path, label="ai.krab.foo")
    d2 = _good_plist(tmp_path, label="ai.krab.foo")
    _write_plist(p1, d1)
    _write_plist(p2, d2)
    # forcibly fix p2 label==filename to pass label-check but still duplicate
    d2["Label"] = "ai.krab.bar"
    _write_plist(p2, d2)
    # set p1 label also bar to create a real duplicate across different files
    d1["Label"] = "ai.krab.foo"
    _write_plist(p1, d1)
    # make p2 use foo label to make a duplicate
    d2["Label"] = "ai.krab.foo"
    _write_plist(p2, d2)

    results = [kvp.validate_plist(p1), kvp.validate_plist(p2)]
    dups = kvp.detect_duplicate_labels(results)
    assert "ai.krab.foo" in dups
    assert len(dups["ai.krab.foo"]) == 2


def test_cli_strict_returns_1(tmp_path, monkeypatch):
    pl = tmp_path / "ai.krab.demo.plist"
    data = _good_plist(tmp_path)
    del data["Label"]
    _write_plist(pl, data)
    report_path = tmp_path / "report.json"

    rc = kvp.main(
        ["--strict", "--report", str(report_path), "--paths", str(pl)]
    )
    assert rc == 1
    report = json.loads(report_path.read_text())
    assert report["summary"]["failed"] >= 1


def test_cli_non_strict_returns_0(tmp_path):
    pl = tmp_path / "ai.krab.demo.plist"
    _write_plist(pl, _good_plist(tmp_path))
    report_path = tmp_path / "report.json"
    rc = kvp.main(["--report", str(report_path), "--paths", str(pl)])
    assert rc == 0
    report = json.loads(report_path.read_text())
    assert report["summary"]["ok"] == 1
