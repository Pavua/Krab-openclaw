# -*- coding: utf-8 -*-
"""
Тесты для src/integrations/macos_automation.py.

Покрываем:
- Pure/static вспомогательные методы (без subprocess)
- Валидацию входных данных и ошибки
- Парсинг выходных данных (get_frontmost_app, list_reminders, list_notes, list_upcoming_calendar_events)
- Мокированные asyncio subprocess вызовы через patch
"""

from __future__ import annotations

import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.integrations.macos_automation import MacOSAutomationError, MacOSAutomationService

# ──────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────


@pytest.fixture
def svc() -> MacOSAutomationService:
    """Экземпляр сервиса для тестов."""
    return MacOSAutomationService(timeout_sec=5.0)


# ──────────────────────────────────────────────────────────────────────
# _datetime_argv — сериализация datetime в список строк
# ──────────────────────────────────────────────────────────────────────


class TestDatetimeArgv:
    def test_базовые_компоненты(self):
        dt = datetime.datetime(2024, 3, 15, 10, 30, 45)
        result = MacOSAutomationService._datetime_argv(dt)
        assert result == ["2024", "3", "15", "10", "30", "45"]

    def test_дополненные_нулём_значения_не_нужны(self):
        # Методу важно что это строки — формат без нулей слева, AppleScript принимает integer
        dt = datetime.datetime(2025, 1, 5, 0, 0, 0)
        result = MacOSAutomationService._datetime_argv(dt)
        assert result == ["2025", "1", "5", "0", "0", "0"]

    def test_возвращает_список_из_6_элементов(self):
        dt = datetime.datetime(2026, 12, 31, 23, 59, 59)
        result = MacOSAutomationService._datetime_argv(dt)
        assert len(result) == 6
        assert all(isinstance(s, str) for s in result)


# ──────────────────────────────────────────────────────────────────────
# _preview_text — компактный preview с обрезкой
# ──────────────────────────────────────────────────────────────────────


class TestPreviewText:
    def test_короткий_текст_не_обрезается(self):
        result = MacOSAutomationService._preview_text("hello", 100)
        assert result == "hello"

    def test_длинный_текст_обрезается_с_многоточием(self):
        text = "a" * 120
        result = MacOSAutomationService._preview_text(text, 100)
        assert len(result) == 100
        assert result.endswith("…")

    def test_перевод_строк_заменяется_пробелом(self):
        text = "line1\nline2\r\nline3"
        result = MacOSAutomationService._preview_text(text, 200)
        assert "\n" not in result
        assert "\r" not in result

    def test_limit_zero_не_обрезает(self):
        text = "a" * 500
        result = MacOSAutomationService._preview_text(text, 0)
        assert result == text

    def test_точно_по_границе_не_добавляет_многоточие(self):
        text = "abc"
        result = MacOSAutomationService._preview_text(text, 3)
        # len == limit, не обрезаем
        assert result == "abc"
        assert "…" not in result


# ──────────────────────────────────────────────────────────────────────
# _resolve_path_or_url — нормализация target для open
# ──────────────────────────────────────────────────────────────────────


class TestResolvePathOrUrl:
    def test_http_url(self):
        result = MacOSAutomationService._resolve_path_or_url("http://example.com")
        assert result == {"kind": "url", "target": "http://example.com"}

    def test_https_url(self):
        result = MacOSAutomationService._resolve_path_or_url("https://example.com/path?q=1")
        assert result["kind"] == "url"

    def test_mailto_url(self):
        result = MacOSAutomationService._resolve_path_or_url("mailto:test@example.com")
        assert result["kind"] == "url"

    def test_пустой_target_вызывает_ошибку(self):
        with pytest.raises(MacOSAutomationError, match="empty_target"):
            MacOSAutomationService._resolve_path_or_url("")

    def test_несуществующий_путь_вызывает_ошибку(self):
        with pytest.raises(MacOSAutomationError, match="Путь не найден"):
            MacOSAutomationService._resolve_path_or_url("/nonexistent/path/very/unlikely")

    def test_абсолютный_существующий_путь(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("data")
        result = MacOSAutomationService._resolve_path_or_url(str(f))
        assert result["kind"] == "path"
        assert result["target"] == str(f)

    def test_tilde_путь_раскрывается(self, monkeypatch):
        # Заменяем os.path.exists чтобы не зависеть от реального home
        with patch("os.path.exists", return_value=True):
            result = MacOSAutomationService._resolve_path_or_url("~/Documents")
        assert result["kind"] == "path"
        assert "~" not in result["target"]


# ──────────────────────────────────────────────────────────────────────
# is_available — проверка инструментов
# ──────────────────────────────────────────────────────────────────────


class TestIsAvailable:
    def test_не_darwin_возвращает_false(self):
        with patch("platform.system", return_value="Linux"):
            assert MacOSAutomationService.is_available() is False

    def test_darwin_без_osascript_возвращает_false(self):
        with patch("platform.system", return_value="Darwin"):
            with patch("shutil.which", return_value=None):
                assert MacOSAutomationService.is_available() is False

    def test_darwin_все_инструменты_есть_возвращает_true(self):
        with patch("platform.system", return_value="Darwin"):
            with patch("shutil.which", return_value="/usr/bin/tool"):
                assert MacOSAutomationService.is_available() is True


# ──────────────────────────────────────────────────────────────────────
# Валидация входных данных (sync guard до subprocess)
# ──────────────────────────────────────────────────────────────────────


class TestValidation:
    @pytest.mark.asyncio
    async def test_open_app_пустое_имя(self, svc):
        with pytest.raises(MacOSAutomationError, match="empty_app_name"):
            await svc.open_app("")

    @pytest.mark.asyncio
    async def test_focus_app_пустое_имя(self, svc):
        with pytest.raises(MacOSAutomationError, match="empty_app_name"):
            await svc.focus_app("")

    @pytest.mark.asyncio
    async def test_focus_app_только_пробелы(self, svc):
        with pytest.raises(MacOSAutomationError, match="empty_app_name"):
            await svc.focus_app("   ")

    @pytest.mark.asyncio
    async def test_type_text_пустой_текст(self, svc):
        with pytest.raises(MacOSAutomationError, match="empty_text"):
            await svc.type_text("")

    @pytest.mark.asyncio
    async def test_type_text_via_clipboard_пустой_текст(self, svc):
        with pytest.raises(MacOSAutomationError, match="empty_text"):
            await svc.type_text_via_clipboard("")

    @pytest.mark.asyncio
    async def test_press_key_пустая_клавиша(self, svc):
        with pytest.raises(MacOSAutomationError, match="empty_key"):
            await svc.press_key("")

    @pytest.mark.asyncio
    async def test_create_reminder_пустой_title(self, svc):
        with pytest.raises(MacOSAutomationError, match="empty_reminder_title"):
            await svc.create_reminder(title="")

    @pytest.mark.asyncio
    async def test_create_note_пустой_title(self, svc):
        with pytest.raises(MacOSAutomationError, match="empty_note_title"):
            await svc.create_note(title="", body="some body")

    @pytest.mark.asyncio
    async def test_create_note_пустой_body(self, svc):
        with pytest.raises(MacOSAutomationError, match="empty_note_body"):
            await svc.create_note(title="Title", body="")

    @pytest.mark.asyncio
    async def test_create_calendar_event_пустой_title(self, svc):
        with pytest.raises(MacOSAutomationError, match="empty_event_title"):
            await svc.create_calendar_event(
                title="",
                start_at=datetime.datetime(2025, 1, 1, 10, 0, 0),
            )

    @pytest.mark.asyncio
    async def test_click_ui_element_пустые_параметры(self, svc):
        with pytest.raises(MacOSAutomationError, match="empty_app_or_element"):
            await svc.click_ui_element("", "OK")

    @pytest.mark.asyncio
    async def test_reveal_in_finder_url_не_принимается(self, svc):
        """reveal_in_finder требует path, не URL."""
        with pytest.raises(MacOSAutomationError, match="finder_reveal_requires_path"):
            await svc.reveal_in_finder("https://example.com")


# ──────────────────────────────────────────────────────────────────────
# _run_command — error handling через мок
# ──────────────────────────────────────────────────────────────────────


class TestRunCommand:
    @pytest.mark.asyncio
    async def test_пустой_список_аргументов(self, svc):
        with pytest.raises(MacOSAutomationError, match="empty_command"):
            await svc._run_command([])

    @pytest.mark.asyncio
    async def test_ненулевой_returncode_вызывает_ошибку(self, svc):
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(return_value=(b"", b"some error"))
        mock_proc.kill = MagicMock()

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            with pytest.raises(MacOSAutomationError, match="command_failed"):
                await svc._run_command(["osascript", "-e", "return 1"])

    @pytest.mark.asyncio
    async def test_успешный_stdout_возвращается(self, svc):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"  hello world  ", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await svc._run_command(["osascript", "-e", "return 1"])
        assert result == "hello world"  # strip()


# ──────────────────────────────────────────────────────────────────────
# Парсинг вывода get_frontmost_app
# ──────────────────────────────────────────────────────────────────────


class TestGetFrontmostAppParsing:
    @pytest.mark.asyncio
    async def test_парсит_две_строки(self, svc):
        with patch.object(svc, "_run_osascript", new=AsyncMock(return_value="Safari\nMain Window")):
            result = await svc.get_frontmost_app()
        assert result == {"app_name": "Safari", "window_title": "Main Window"}

    @pytest.mark.asyncio
    async def test_одна_строка_без_заголовка(self, svc):
        with patch.object(svc, "_run_osascript", new=AsyncMock(return_value="Finder")):
            result = await svc.get_frontmost_app()
        assert result["app_name"] == "Finder"
        assert result["window_title"] == ""


# ──────────────────────────────────────────────────────────────────────
# Парсинг list_reminders (формат "list||title||due")
# ──────────────────────────────────────────────────────────────────────


class TestListRemindersParsing:
    @pytest.mark.asyncio
    async def test_парсит_строки_с_разделителем(self, svc):
        raw = "Work||Buy milk||Monday, 14 April 2025 10:00:00\nPersonal||Call doctor||"
        with patch.object(svc, "_run_osascript", new=AsyncMock(return_value=raw)):
            rows = await svc.list_reminders()
        assert len(rows) == 2
        assert rows[0] == {
            "list_name": "Work",
            "title": "Buy milk",
            "due_label": "Monday, 14 April 2025 10:00:00",
        }
        assert rows[1] == {"list_name": "Personal", "title": "Call doctor", "due_label": ""}

    @pytest.mark.asyncio
    async def test_пустой_вывод_возвращает_пустой_список(self, svc):
        with patch.object(svc, "_run_osascript", new=AsyncMock(return_value="")):
            rows = await svc.list_reminders()
        assert rows == []

    @pytest.mark.asyncio
    async def test_неполные_строки_игнорируются(self, svc):
        raw = "bad_line_without_separators\nWork||Task||"
        with patch.object(svc, "_run_osascript", new=AsyncMock(return_value=raw)):
            rows = await svc.list_reminders()
        assert len(rows) == 1


# ──────────────────────────────────────────────────────────────────────
# Парсинг list_notes (формат "account||folder||title")
# ──────────────────────────────────────────────────────────────────────


class TestListNotesParsing:
    @pytest.mark.asyncio
    async def test_парсит_строки_заметок(self, svc):
        raw = "iCloud||Notes||My Note\niCloud||Work||Meeting notes"
        with patch.object(svc, "_run_osascript", new=AsyncMock(return_value=raw)):
            rows = await svc.list_notes()
        assert len(rows) == 2
        assert rows[0] == {"account_name": "iCloud", "folder_name": "Notes", "title": "My Note"}


# ──────────────────────────────────────────────────────────────────────
# Парсинг list_upcoming_calendar_events
# ──────────────────────────────────────────────────────────────────────


class TestCalendarEventsParsing:
    @pytest.mark.asyncio
    async def test_парсит_события(self, svc):
        raw = "Work||Standup||Monday, 14 April 2025 10:00:00\nPersonal||Gym||Tuesday, 15 April 2025 08:00:00"
        with patch.object(svc, "_run_osascript", new=AsyncMock(return_value=raw)):
            rows = await svc.list_upcoming_calendar_events()
        assert len(rows) == 2
        assert rows[0]["calendar_name"] == "Work"
        assert rows[0]["title"] == "Standup"


# ──────────────────────────────────────────────────────────────────────
# list_running_apps — фильтрация и limit
# ──────────────────────────────────────────────────────────────────────


class TestListRunningApps:
    @pytest.mark.asyncio
    async def test_limit_обрезает_список(self, svc):
        raw = "Safari\nFinder\nTerminal\nSlack\nChrome"
        with patch.object(svc, "_run_osascript", new=AsyncMock(return_value=raw)):
            apps = await svc.list_running_apps(limit=3)
        assert apps == ["Safari", "Finder", "Terminal"]

    @pytest.mark.asyncio
    async def test_limit_zero_возвращает_всё(self, svc):
        raw = "Safari\nFinder\nTerminal"
        with patch.object(svc, "_run_osascript", new=AsyncMock(return_value=raw)):
            apps = await svc.list_running_apps(limit=0)
        assert len(apps) == 3

    @pytest.mark.asyncio
    async def test_пустые_строки_отфильтровываются(self, svc):
        raw = "Safari\n\n  \nFinder"
        with patch.object(svc, "_run_osascript", new=AsyncMock(return_value=raw)):
            apps = await svc.list_running_apps()
        assert apps == ["Safari", "Finder"]


# ──────────────────────────────────────────────────────────────────────
# open_target — мок subprocess + _resolve_path_or_url
# ──────────────────────────────────────────────────────────────────────


class TestOpenTarget:
    @pytest.mark.asyncio
    async def test_открывает_url(self, svc):
        with patch.object(svc, "_run_command", new=AsyncMock(return_value="")) as mock_cmd:
            result = await svc.open_target("https://example.com")
        assert result["kind"] == "url"
        mock_cmd.assert_called_once_with(["open", "https://example.com"])

    @pytest.mark.asyncio
    async def test_пустой_target_вызывает_ошибку(self, svc):
        with pytest.raises(MacOSAutomationError, match="empty_target"):
            await svc.open_target("")


# ──────────────────────────────────────────────────────────────────────
# show_notification — аргументы без subtitle и с subtitle
# ──────────────────────────────────────────────────────────────────────


class TestShowNotification:
    @pytest.mark.asyncio
    async def test_без_subtitle(self, svc):
        with patch.object(svc, "_run_osascript", new=AsyncMock(return_value="ok")) as mock_osa:
            await svc.show_notification(title="Test", message="Body")
        # argv должен содержать только title + message
        call_args = mock_osa.call_args
        positional = call_args[0]  # (script, *argv)
        argv = positional[1:]
        assert "Test" in argv
        assert "Body" in argv
        assert len(argv) == 2

    @pytest.mark.asyncio
    async def test_с_subtitle(self, svc):
        with patch.object(svc, "_run_osascript", new=AsyncMock(return_value="ok")) as mock_osa:
            await svc.show_notification(title="Test", message="Body", subtitle="Sub")
        call_args = mock_osa.call_args
        argv = call_args[0][1:]
        assert "Sub" in argv
        assert len(argv) == 3


# ──────────────────────────────────────────────────────────────────────
# health_check — при недоступном darwin
# ──────────────────────────────────────────────────────────────────────


class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_не_darwin_возвращает_blocked(self, svc):
        with patch("platform.system", return_value="Linux"):
            result = await svc.health_check()
        assert result["ok"] is False
        assert result["blocked"] is True
        assert "Linux" in result["error"]

    @pytest.mark.asyncio
    async def test_darwin_осascript_работает(self, svc):
        with patch("platform.system", return_value="Darwin"):
            with patch("shutil.which", return_value="/usr/bin/tool"):
                with patch.object(svc, "_run_osascript", new=AsyncMock(return_value="ok")):
                    result = await svc.health_check()
        assert result["ok"] is True
        assert result["blocked"] is False
        assert "tools" in result

    @pytest.mark.asyncio
    async def test_osascript_ошибка_возвращает_не_ok(self, svc):
        with patch("platform.system", return_value="Darwin"):
            with patch("shutil.which", return_value="/usr/bin/tool"):
                with patch.object(
                    svc, "_run_osascript", new=AsyncMock(side_effect=MacOSAutomationError("fail"))
                ):
                    result = await svc.health_check()
        assert result["ok"] is False
        assert result["blocked"] is False


# ──────────────────────────────────────────────────────────────────────
# create_reminder — argv с due_at и без
# ──────────────────────────────────────────────────────────────────────


class TestCreateReminderArgv:
    @pytest.mark.asyncio
    async def test_без_due_at_два_аргумента(self, svc):
        with patch.object(
            svc, "_run_osascript", new=AsyncMock(return_value="id-123\nWork")
        ) as mock_osa:
            result = await svc.create_reminder(title="Buy milk", list_name="Work")
        call_args = mock_osa.call_args[0]
        argv = call_args[1:]
        assert argv == ("Work", "Buy milk")
        assert result == {"id": "id-123", "list_name": "Work"}

    @pytest.mark.asyncio
    async def test_с_due_at_8_дополнительных_аргументов(self, svc):
        dt = datetime.datetime(2025, 6, 15, 9, 30, 0)
        with patch.object(
            svc, "_run_osascript", new=AsyncMock(return_value="id-456\nWork")
        ) as mock_osa:
            await svc.create_reminder(title="Meeting", due_at=dt, list_name="Work")
        call_args = mock_osa.call_args[0]
        argv = call_args[1:]
        # list_name + title + 6 datetime компонентов = 8
        assert len(argv) == 8
        assert argv[2] == "2025"  # year
        assert argv[3] == "6"  # month
        assert argv[4] == "15"  # day


# ──────────────────────────────────────────────────────────────────────
# status — soft degraded mode (все _run_osascript бросают)
# ──────────────────────────────────────────────────────────────────────


class TestStatusDegradedMode:
    @pytest.mark.asyncio
    async def test_все_подкоманды_падают_но_status_возвращается(self, svc):
        with patch("platform.system", return_value="Darwin"):
            with patch("shutil.which", return_value="/usr/bin/tool"):
                with patch.object(
                    svc, "_run_osascript", new=AsyncMock(side_effect=MacOSAutomationError("err"))
                ):
                    with patch.object(
                        svc, "_run_command", new=AsyncMock(side_effect=MacOSAutomationError("err"))
                    ):
                        result = await svc.status()
        # Не упало — вернулся dict с предупреждениями
        assert isinstance(result, dict)
        assert result["available"] is True
        assert len(result["warnings"]) > 0
