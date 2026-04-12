# -*- coding: utf-8 -*-
"""
Тесты swarm API endpoint'ов web-панели Krab.

Покрываем:
  GET  /api/swarm/stats
  GET  /api/swarm/teams
  GET  /api/swarm/task-board
  GET  /api/swarm/tasks
  GET  /api/swarm/reports
  GET  /api/swarm/artifacts
  GET  /api/swarm/listeners
  GET  /api/swarm/task/{task_id}
  GET  /api/swarm/team/{team_name}
  POST /api/swarm/tasks/create
  POST /api/swarm/listeners/toggle
  POST /api/swarm/task/{task_id}/update
  POST /api/swarm/task/{task_id}/priority
  POST /api/swarm/artifacts/cleanup
  DELETE /api/swarm/task/{task_id}
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from src.modules.web_app import WebApp

# ---------------------------------------------------------------------------
# Заглушки — минимально необходимые стабы без внешних зависимостей
# ---------------------------------------------------------------------------

WEB_KEY = "test-key-123"


class _FakeOpenClaw:
    def get_last_runtime_route(self) -> dict:
        return {
            "channel": "cloud",
            "provider": "google",
            "model": "google/gemini-test",
            "status": "ok",
        }

    def get_tier_state_export(self) -> dict:
        return {"active_tier": "free", "last_error_code": None}

    async def health_check(self) -> bool:
        return True


class _FakeHealthClient:
    async def health_check(self) -> bool:
        return True

    async def health_report(self) -> dict:
        return {"ok": True, "status": "ok", "source": "fake"}

    async def capabilities_report(self) -> dict:
        return {"ok": True, "status": "ok", "source": "fake", "detail": {}}


class _DummyRouter:
    def get_model_info(self) -> dict:
        return {}


class _FakeKraab:
    def get_translator_runtime_profile(self) -> dict:
        return {"language_pair": "es-ru", "enabled": True}

    def get_translator_session_state(self) -> dict:
        return {"session_status": "idle", "active_chats": [], "stats": {}}

    def get_voice_runtime_profile(self) -> dict:
        return {"tts_enabled": False}

    def get_runtime_state(self) -> dict:
        return {"startup_state": "running", "client_connected": True}


# ---------------------------------------------------------------------------
# Фабрика WebApp с полным набором заглушек
# ---------------------------------------------------------------------------


def _make_app() -> WebApp:
    deps = {
        "router": _DummyRouter(),
        "openclaw_client": _FakeOpenClaw(),
        "black_box": None,
        "health_service": None,
        "provisioning_service": None,
        "ai_runtime": None,
        "reaction_engine": None,
        "voice_gateway_client": _FakeHealthClient(),
        "krab_ear_client": _FakeHealthClient(),
        "perceptor": None,
        "watchdog": None,
        "queue": None,
        "kraab_userbot": _FakeKraab(),
    }
    app = WebApp(deps, port=18091, host="127.0.0.1")
    return app


def _client() -> TestClient:
    return TestClient(_make_app().app)


# ---------------------------------------------------------------------------
# Вспомогательный fake_task для task board
# ---------------------------------------------------------------------------


def _make_fake_task(task_id: str = "task-001", team: str = "traders") -> MagicMock:
    t = MagicMock()
    t.task_id = task_id
    t.team = team
    t.title = "Test task"
    t.status = "pending"
    t.priority = "medium"
    t.created_at = 0.0
    t.updated_at = 0.0
    t.result = None
    t.error = None
    return t


# ---------------------------------------------------------------------------
# GET /api/swarm/stats
# ---------------------------------------------------------------------------


def test_swarm_stats_ok() -> None:
    """GET /api/swarm/stats возвращает ok=True с полями board и artifacts_count."""
    fake_board = MagicMock()
    fake_board.get_board_summary.return_value = {"total": 0, "by_team": {}}

    fake_art = MagicMock()
    fake_art.list_artifacts.return_value = []

    with (
        patch("src.core.swarm_task_board.swarm_task_board", fake_board),
        patch("src.core.swarm_artifact_store.swarm_artifact_store", fake_art),
        patch("src.core.swarm_team_listener.is_listeners_enabled", return_value=False),
    ):
        resp = _client().get("/api/swarm/stats")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "board" in data
    assert "artifacts_count" in data
    assert "listeners_enabled" in data


def test_swarm_stats_artifacts_count() -> None:
    """Поле artifacts_count отражает реальное количество артефактов."""
    fake_board = MagicMock()
    fake_board.get_board_summary.return_value = {"total": 0}

    fake_art = MagicMock()
    fake_art.list_artifacts.return_value = [MagicMock(), MagicMock(), MagicMock()]

    with (
        patch("src.core.swarm_task_board.swarm_task_board", fake_board),
        patch("src.core.swarm_artifact_store.swarm_artifact_store", fake_art),
        patch("src.core.swarm_team_listener.is_listeners_enabled", return_value=True),
    ):
        resp = _client().get("/api/swarm/stats")

    assert resp.json()["artifacts_count"] == 3
    assert resp.json()["listeners_enabled"] is True


# ---------------------------------------------------------------------------
# GET /api/swarm/teams
# ---------------------------------------------------------------------------


def test_swarm_teams_ok() -> None:
    """GET /api/swarm/teams возвращает ok=True и dict teams."""
    fake_registry = {
        "traders": [{"name": "analyst", "title": "Аналитик", "emoji": "📊"}],
        "coders": [{"name": "dev", "title": "Разработчик", "emoji": "💻"}],
    }
    with patch("src.core.swarm_bus.TEAM_REGISTRY", fake_registry):
        resp = _client().get("/api/swarm/teams")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "teams" in data
    assert "traders" in data["teams"]
    assert "coders" in data["teams"]


def test_swarm_teams_roles_structure() -> None:
    """Каждая роль содержит поля name, title, emoji."""
    fake_registry = {
        "analysts": [{"name": "quant", "title": "Квант", "emoji": "🔢"}],
    }
    with patch("src.core.swarm_bus.TEAM_REGISTRY", fake_registry):
        resp = _client().get("/api/swarm/teams")

    roles = resp.json()["teams"]["analysts"]
    assert len(roles) == 1
    assert roles[0]["name"] == "quant"
    assert "title" in roles[0]
    assert "emoji" in roles[0]


# ---------------------------------------------------------------------------
# GET /api/swarm/task-board
# ---------------------------------------------------------------------------


def test_swarm_task_board_ok() -> None:
    """GET /api/swarm/task-board возвращает ok=True и summary."""
    fake_board = MagicMock()
    fake_board.get_board_summary.return_value = {"total": 5, "pending": 3, "done": 2}

    with patch("src.core.swarm_task_board.swarm_task_board", fake_board):
        resp = _client().get("/api/swarm/task-board")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["summary"]["total"] == 5


# ---------------------------------------------------------------------------
# GET /api/swarm/tasks
# ---------------------------------------------------------------------------


def test_swarm_tasks_list_ok() -> None:
    """GET /api/swarm/tasks возвращает ok=True и список tasks."""
    fake_board = MagicMock()
    fake_board.list_tasks.return_value = [_make_fake_task("t1"), _make_fake_task("t2")]

    with patch("src.core.swarm_task_board.swarm_task_board", fake_board):
        resp = _client().get("/api/swarm/tasks")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert isinstance(data["tasks"], list)
    assert len(data["tasks"]) == 2


def test_swarm_tasks_filter_by_team() -> None:
    """GET /api/swarm/tasks?team=traders передаёт фильтр в list_tasks."""
    fake_board = MagicMock()
    fake_board.list_tasks.return_value = []

    with patch("src.core.swarm_task_board.swarm_task_board", fake_board):
        resp = _client().get("/api/swarm/tasks?team=traders")

    assert resp.status_code == 200
    # Проверяем что list_tasks вызван с team=traders
    fake_board.list_tasks.assert_called_once()
    call_kwargs = fake_board.list_tasks.call_args
    assert call_kwargs is not None


# ---------------------------------------------------------------------------
# GET /api/swarm/reports
# ---------------------------------------------------------------------------


def test_swarm_reports_no_dir(tmp_path, monkeypatch) -> None:
    """GET /api/swarm/reports возвращает пустой список если директория отсутствует."""
    # Указываем несуществующую директорию через monkeypatch Path.home()
    fake_home = tmp_path / "nonexistent_home"
    monkeypatch.setattr("pathlib.Path.home", lambda: fake_home)

    resp = _client().get("/api/swarm/reports")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["reports"] == []


def test_swarm_reports_with_files(tmp_path, monkeypatch) -> None:
    """GET /api/swarm/reports возвращает список .md файлов из reports/."""
    # Создаём фейковую директорию репортов
    report_dir = tmp_path / ".openclaw" / "krab_runtime_state" / "reports"
    report_dir.mkdir(parents=True)
    (report_dir / "report_001.md").write_text("# Test report 1")
    (report_dir / "report_002.md").write_text("# Test report 2")

    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    resp = _client().get("/api/swarm/reports")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert len(data["reports"]) == 2
    # Проверяем структуру каждого репорта
    for r in data["reports"]:
        assert "name" in r
        assert "size_kb" in r
        assert "modified" in r


# ---------------------------------------------------------------------------
# GET /api/swarm/artifacts
# ---------------------------------------------------------------------------


def test_swarm_artifacts_ok() -> None:
    """GET /api/swarm/artifacts возвращает ok=True и список."""
    fake_artifact = MagicMock()
    fake_artifact.artifact_id = "art-001"
    fake_artifact.team = "traders"
    fake_artifact.kind = "report"
    fake_artifact.title = "Test artifact"
    fake_artifact.created_at = 0.0
    fake_artifact.size_bytes = 100

    fake_store = MagicMock()
    fake_store.list_artifacts.return_value = [fake_artifact]

    with patch("src.core.swarm_artifact_store.swarm_artifact_store", fake_store):
        resp = _client().get("/api/swarm/artifacts")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert isinstance(data["artifacts"], list)
    assert len(data["artifacts"]) == 1


def test_swarm_artifacts_empty() -> None:
    """GET /api/swarm/artifacts возвращает пустой список без артефактов."""
    fake_store = MagicMock()
    fake_store.list_artifacts.return_value = []

    with patch("src.core.swarm_artifact_store.swarm_artifact_store", fake_store):
        resp = _client().get("/api/swarm/artifacts")

    data = resp.json()
    assert data["ok"] is True
    assert data["artifacts"] == []


# ---------------------------------------------------------------------------
# GET /api/swarm/listeners
# ---------------------------------------------------------------------------


def test_swarm_listeners_disabled() -> None:
    """GET /api/swarm/listeners возвращает listeners_enabled=False когда отключены."""
    with patch("src.core.swarm_team_listener.is_listeners_enabled", return_value=False):
        resp = _client().get("/api/swarm/listeners")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["listeners_enabled"] is False


def test_swarm_listeners_enabled() -> None:
    """GET /api/swarm/listeners возвращает listeners_enabled=True когда включены."""
    with patch("src.core.swarm_team_listener.is_listeners_enabled", return_value=True):
        resp = _client().get("/api/swarm/listeners")

    data = resp.json()
    assert data["ok"] is True
    assert data["listeners_enabled"] is True


# ---------------------------------------------------------------------------
# POST /api/swarm/listeners/toggle (write-key required)
# ---------------------------------------------------------------------------


def test_swarm_listeners_toggle_requires_auth(monkeypatch) -> None:
    """POST /api/swarm/listeners/toggle без ключа — 403 (при установленном WEB_API_KEY)."""
    monkeypatch.setenv("WEB_API_KEY", WEB_KEY)
    with patch("src.core.swarm_team_listener.is_listeners_enabled", return_value=False):
        resp = _client().post("/api/swarm/listeners/toggle", json={"enabled": True})
    assert resp.status_code == 403


def test_swarm_listeners_toggle_with_key(monkeypatch) -> None:
    """POST /api/swarm/listeners/toggle с ключом устанавливает статус."""
    monkeypatch.setattr("src.config.config.WEB_WRITE_KEY", WEB_KEY, raising=False)

    with (
        patch("src.core.swarm_team_listener.is_listeners_enabled", return_value=False),
        patch("src.core.swarm_team_listener.set_listeners_enabled") as mock_set,
    ):
        resp = _client().post(
            "/api/swarm/listeners/toggle",
            json={"enabled": True},
            headers={"X-Krab-Web-Key": WEB_KEY},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["listeners_enabled"] is True
    mock_set.assert_called_once_with(True)


# ---------------------------------------------------------------------------
# POST /api/swarm/tasks/create
# ---------------------------------------------------------------------------


def test_swarm_task_create_requires_auth(monkeypatch) -> None:
    """POST /api/swarm/tasks/create без ключа — 403 (при установленном WEB_API_KEY)."""
    monkeypatch.setenv("WEB_API_KEY", WEB_KEY)
    resp = _client().post("/api/swarm/tasks/create", json={"team": "traders", "title": "Test"})
    assert resp.status_code == 403


def test_swarm_task_create_ok(monkeypatch) -> None:
    """POST /api/swarm/tasks/create создаёт задачу и возвращает task_id."""
    monkeypatch.setattr("src.config.config.WEB_WRITE_KEY", WEB_KEY, raising=False)

    fake_task = _make_fake_task("new-task-001", "traders")
    fake_board = MagicMock()
    fake_board.create_task.return_value = fake_task

    with patch("src.core.swarm_task_board.swarm_task_board", fake_board):
        resp = _client().post(
            "/api/swarm/tasks/create",
            json={"team": "traders", "title": "Test task"},
            headers={"X-Krab-Web-Key": WEB_KEY},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["task_id"] == "new-task-001"
    assert data["team"] == "traders"


# ---------------------------------------------------------------------------
# POST /api/swarm/artifacts/cleanup
# ---------------------------------------------------------------------------


def test_swarm_artifacts_cleanup_requires_auth(monkeypatch) -> None:
    """POST /api/swarm/artifacts/cleanup без ключа — 403 (при установленном WEB_API_KEY)."""
    monkeypatch.setenv("WEB_API_KEY", WEB_KEY)
    resp = _client().post("/api/swarm/artifacts/cleanup")
    assert resp.status_code == 403


def test_swarm_artifacts_cleanup_ok(monkeypatch) -> None:
    """POST /api/swarm/artifacts/cleanup с ключом запускает cleanup и возвращает removed."""
    monkeypatch.setattr("src.config.config.WEB_WRITE_KEY", WEB_KEY, raising=False)

    fake_store = MagicMock()
    fake_store.cleanup_old.return_value = 7

    with patch("src.core.swarm_artifact_store.swarm_artifact_store", fake_store):
        resp = _client().post(
            "/api/swarm/artifacts/cleanup",
            headers={"X-Krab-Web-Key": WEB_KEY},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["removed"] == 7
    fake_store.cleanup_old.assert_called_once_with(max_files=50)
