# -*- coding: utf-8 -*-
"""
macOS automation service для Краба.

Зачем нужен этот модуль:
- даёт единый, правдивый слой системных действий на macOS вместо разрозненных
  `osascript`-вызовов по проекту;
- служит базой для owner-команд, будущей проактивности и безопасных desktop-actions;
- отделяет системные операции (clipboard, notification, Finder, apps) от логики
  userbot и web/runtime-слоя.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import platform
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ..core.logger import get_logger

logger = get_logger(__name__)


class MacOSAutomationError(RuntimeError):
    """Ошибка выполнения системного действия macOS."""


class MacOSAutomationService:
    """
    Безопасный слой базовой автоматизации macOS.

    Почему набор действий пока именно такой:
    - это самые полезные owner-операции с низким риском побочных эффектов;
    - они хорошо покрывают desktop-control сценарии: буфер обмена, уведомления,
      открытие приложений/файлов и быстрый контекст по активному окну;
    - их можно живо проверить без навязчивых изменений пользовательской среды.
    """

    def __init__(self, timeout_sec: float = 12.0) -> None:
        self.timeout_sec = max(1.0, float(timeout_sec))

    @staticmethod
    def is_available() -> bool:
        """Проверяет, есть ли в текущем runtime базовые macOS-инструменты."""
        if platform.system() != "Darwin":
            return False
        required_bins = ("osascript", "open", "pbcopy", "pbpaste")
        return all(bool(shutil.which(name)) for name in required_bins)

    async def _run_command(
        self,
        args: list[str],
        *,
        input_text: str | None = None,
        timeout_sec: float | None = None,
    ) -> str:
        """Запускает локальную команду и возвращает stdout."""
        if not args:
            raise MacOSAutomationError("empty_command")
        timeout = max(1.0, float(timeout_sec or self.timeout_sec))
        stdin = asyncio.subprocess.PIPE if input_text is not None else asyncio.subprocess.DEVNULL
        process = await asyncio.create_subprocess_exec(
            *args,
            stdin=stdin,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(
                    None if input_text is None else input_text.encode("utf-8")
                ),
                timeout=timeout,
            )
        except asyncio.TimeoutError as exc:
            process.kill()
            await process.communicate()
            raise MacOSAutomationError(f"timeout:{' '.join(args)}") from exc

        if process.returncode != 0:
            error_text = (stderr or b"").decode("utf-8", errors="replace").strip()
            raise MacOSAutomationError(
                error_text or f"command_failed:{process.returncode}:{' '.join(args)}"
            )
        return (stdout or b"").decode("utf-8", errors="replace").strip()

    async def _run_osascript(
        self,
        script: str,
        *,
        argv: list[str] | None = None,
        timeout_sec: float | None = None,
    ) -> str:
        """Запускает AppleScript через `osascript` с аргументами."""
        cmd = ["osascript", "-e", script]
        if argv:
            cmd.extend(argv)
        return await self._run_command(cmd, timeout_sec=timeout_sec)

    @staticmethod
    def _datetime_argv(value: datetime) -> list[str]:
        """Сериализует datetime в argv для AppleScript без locale-парсинга."""
        dt = value.astimezone()
        return [
            str(int(dt.year)),
            str(int(dt.month)),
            str(int(dt.day)),
            str(int(dt.hour)),
            str(int(dt.minute)),
            str(int(dt.second)),
        ]

    @staticmethod
    def _preview_text(text: str, limit: int = 120) -> str:
        """Возвращает компактный preview текста для статусов и ответов."""
        raw = str(text or "").replace("\r", " ").replace("\n", " ").strip()
        if len(raw) <= limit:
            return raw
        return raw[: max(0, limit - 1)] + "…"

    @staticmethod
    def _resolve_path_or_url(target: str) -> tuple[str, str]:
        """
        Нормализует пользовательскую цель для `open`.

        Поддерживаем:
        - `http/https/mailto/tel/file` URL;
        - абсолютные пути;
        - `~/...` и относительные пути от текущего рабочего каталога runtime.
        """
        value = str(target or "").strip()
        if not value:
            raise ValueError("empty_target")

        parsed = urlparse(value)
        if parsed.scheme in {"http", "https", "mailto", "tel", "file"}:
            return "url", value

        path = Path(value).expanduser()
        if not path.is_absolute():
            path = (Path.cwd() / path).resolve()
        if not path.exists():
            raise FileNotFoundError(f"Путь не найден: {path}")
        return "path", str(path)

    async def get_frontmost_app(self) -> dict[str, str]:
        """Возвращает активное приложение и заголовок переднего окна."""
        script = r'''
tell application "System Events"
    set frontApp to first application process whose frontmost is true
    set appName to name of frontApp
    set windowTitle to ""
    try
        set windowTitle to name of front window of frontApp
    end try
    return appName & linefeed & windowTitle
end tell
'''
        output = await self._run_osascript(script)
        app_name, _, window_title = output.partition("\n")
        return {
            "app_name": str(app_name or "").strip(),
            "window_title": str(window_title or "").strip(),
        }

    async def list_running_apps(self, limit: int = 12) -> list[str]:
        """Возвращает список видимых пользовательских приложений."""
        script = r'''
tell application "System Events"
    set appNames to name of every application process whose background only is false
    set AppleScript's text item delimiters to linefeed
    return appNames as text
end tell
'''
        output = await self._run_osascript(script)
        names = [item.strip() for item in output.splitlines() if item.strip()]
        if limit > 0:
            return names[:limit]
        return names

    async def get_clipboard_text(self) -> str:
        """Читает текстовый clipboard через `pbpaste`."""
        return await self._run_command(["pbpaste"])

    async def set_clipboard_text(self, text: str) -> None:
        """Записывает текст в clipboard через `pbcopy`."""
        await self._run_command(["pbcopy"], input_text=str(text or ""))

    async def show_notification(
        self,
        *,
        title: str,
        message: str,
        subtitle: str = "",
    ) -> None:
        """Показывает локальное системное уведомление macOS."""
        script = r'''
on run argv
    set theTitle to item 1 of argv
    set theBody to item 2 of argv
    set theSubtitle to ""
    if (count of argv) > 2 then
        set theSubtitle to item 3 of argv
    end if
    if theSubtitle is "" then
        display notification theBody with title theTitle
    else
        display notification theBody with title theTitle subtitle theSubtitle
    end if
    return "ok"
end run
'''
        await self._run_osascript(
            script,
            argv=[str(title or "Краб"), str(message or ""), str(subtitle or "")],
        )

    async def open_app(self, app_name: str) -> str:
        """Открывает приложение через `open -a`."""
        target = str(app_name or "").strip()
        if not target:
            raise ValueError("empty_app_name")
        await self._run_command(["open", "-a", target])
        return target

    async def open_target(self, target: str) -> dict[str, str]:
        """Открывает URL или путь штатным `open`."""
        kind, normalized = self._resolve_path_or_url(target)
        await self._run_command(["open", normalized])
        return {"kind": kind, "target": normalized}

    async def reveal_in_finder(self, target: str) -> str:
        """Показывает файл/папку в Finder через `open -R`."""
        kind, normalized = self._resolve_path_or_url(target)
        if kind != "path":
            raise ValueError("finder_reveal_requires_path")
        await self._run_command(["open", "-R", normalized])
        return normalized

    async def list_reminder_lists(self) -> list[str]:
        """Возвращает имена списков Reminders."""
        script = r'''
tell application "Reminders"
    set listNames to name of every list
    set AppleScript's text item delimiters to linefeed
    return listNames as text
end tell
'''
        output = await self._run_osascript(script)
        return [item.strip() for item in output.splitlines() if item.strip()]

    async def list_reminders(self, limit: int = 10) -> list[dict[str, str]]:
        """Возвращает несколько ближайших незавершённых reminder-элементов."""
        script = r'''
on run argv
    set maxItems to (item 1 of argv as integer)
    set outLines to {}
    tell application "Reminders"
        repeat with targetList in every list
            repeat with reminderRef in reminders of targetList
                try
                    if completed of reminderRef is false then
                        set reminderName to name of reminderRef as text
                        set dueLabel to ""
                        try
                            set dueValue to due date of reminderRef
                            if dueValue is not missing value then
                                set dueLabel to ((date string of dueValue) & " " & (time string of dueValue))
                            end if
                        end try
                        set end of outLines to ((name of targetList as text) & "||" & reminderName & "||" & dueLabel)
                        if (count of outLines) is greater than or equal to maxItems then exit repeat
                    end if
                end try
            end repeat
            if (count of outLines) is greater than or equal to maxItems then exit repeat
        end repeat
    end tell
    set AppleScript's text item delimiters to linefeed
    return outLines as text
end run
'''
        output = await self._run_osascript(script, argv=[str(max(1, int(limit or 1)))])
        rows: list[dict[str, str]] = []
        for line in output.splitlines():
            if not line.strip():
                continue
            list_name, _, tail = line.partition("||")
            title, _, due_label = tail.partition("||")
            rows.append(
                {
                    "list_name": list_name.strip(),
                    "title": title.strip(),
                    "due_label": due_label.strip(),
                }
            )
        return rows

    async def create_reminder(
        self,
        *,
        title: str,
        due_at: datetime | None = None,
        list_name: str = "",
    ) -> dict[str, str]:
        """Создаёт reminder в Reminders и возвращает его id/список."""
        reminder_title = str(title or "").strip()
        if not reminder_title:
            raise ValueError("empty_reminder_title")
        script = r'''
on run argv
    set listNameArg to item 1 of argv
    set reminderTitle to item 2 of argv
    tell application "Reminders"
        if listNameArg is "" then
            set targetList to first list
        else
            set targetList to first list whose name is listNameArg
        end if
        set newReminder to make new reminder at end of reminders of targetList with properties {name:reminderTitle}
        if (count of argv) is greater than 2 then
            set y to (item 3 of argv as integer)
            set m to (item 4 of argv as integer)
            set d to (item 5 of argv as integer)
            set hh to (item 6 of argv as integer)
            set mm to (item 7 of argv as integer)
            set ss to (item 8 of argv as integer)
            set monthNames to {January, February, March, April, May, June, July, August, September, October, November, December}
            set dueValue to current date
            set year of dueValue to y
            set month of dueValue to item m of monthNames
            set day of dueValue to d
            set time of dueValue to ((hh * hours) + (mm * minutes) + ss)
            set due date of newReminder to dueValue
        end if
        return ((id of newReminder as text) & linefeed & (name of targetList as text))
    end tell
end run
'''
        argv = [list_name.strip(), reminder_title]
        if due_at is not None:
            argv.extend(self._datetime_argv(due_at))
        output = await self._run_osascript(script, argv=argv)
        reminder_id, _, resolved_list = output.partition("\n")
        return {"id": reminder_id.strip(), "list_name": resolved_list.strip()}

    async def list_note_folders(self) -> list[str]:
        """Возвращает имена папок default account в Notes."""
        script = r'''
tell application "Notes"
    tell default account
        set folderNames to name of every folder
        set AppleScript's text item delimiters to linefeed
        return folderNames as text
    end tell
end tell
'''
        output = await self._run_osascript(script)
        return [item.strip() for item in output.splitlines() if item.strip()]

    async def list_notes(self, limit: int = 10) -> list[dict[str, str]]:
        """Возвращает несколько заметок с аккаунтом/папкой/заголовком."""
        script = r'''
on run argv
    set maxItems to (item 1 of argv as integer)
    set outLines to {}
    tell application "Notes"
        repeat with acc in accounts
            repeat with folderRef in folders of acc
                repeat with noteRef in notes of folderRef
                    set end of outLines to ((name of acc as text) & "||" & (name of folderRef as text) & "||" & (name of noteRef as text))
                    if (count of outLines) is greater than or equal to maxItems then exit repeat
                end repeat
                if (count of outLines) is greater than or equal to maxItems then exit repeat
            end repeat
            if (count of outLines) is greater than or equal to maxItems then exit repeat
        end repeat
    end tell
    set AppleScript's text item delimiters to linefeed
    return outLines as text
end run
'''
        output = await self._run_osascript(script, argv=[str(max(1, int(limit or 1)))])
        rows: list[dict[str, str]] = []
        for line in output.splitlines():
            if not line.strip():
                continue
            account_name, _, tail = line.partition("||")
            folder_name, _, note_title = tail.partition("||")
            rows.append(
                {
                    "account_name": account_name.strip(),
                    "folder_name": folder_name.strip(),
                    "title": note_title.strip(),
                }
            )
        return rows

    async def create_note(
        self,
        *,
        title: str,
        body: str,
        folder_name: str = "Notes",
    ) -> dict[str, str]:
        """Создаёт заметку в Notes и возвращает её id/папку."""
        note_title = str(title or "").strip()
        note_body = str(body or "").strip()
        if not note_title:
            raise ValueError("empty_note_title")
        if not note_body:
            raise ValueError("empty_note_body")
        script = r'''
on run argv
    set folderNameArg to item 1 of argv
    set noteTitle to item 2 of argv
    set noteBody to item 3 of argv
    tell application "Notes"
        tell default account
            if folderNameArg is "" then
                set targetFolder to first folder
            else
                set targetFolder to first folder whose name is folderNameArg
            end if
            set newNote to make new note at targetFolder with properties {name:noteTitle, body:noteBody}
            return ((id of newNote as text) & linefeed & (name of targetFolder as text))
        end tell
    end tell
end run
'''
        output = await self._run_osascript(
            script,
            argv=[str(folder_name or "").strip(), note_title, note_body],
        )
        note_id, _, resolved_folder = output.partition("\n")
        return {"id": note_id.strip(), "folder_name": resolved_folder.strip()}

    async def list_calendars(self) -> list[str]:
        """Возвращает список календарей из приложения Calendar."""
        script = r'''
tell application "Calendar"
    set calNames to name of every calendar
    set AppleScript's text item delimiters to linefeed
    return calNames as text
end tell
'''
        output = await self._run_osascript(script)
        return [item.strip() for item in output.splitlines() if item.strip()]

    async def list_upcoming_calendar_events(
        self,
        limit: int = 10,
        days_ahead: int = 7,
        calendar_limit: int = 8,
    ) -> list[dict[str, str]]:
        """
        Возвращает ближайшие события из Calendar.

        Почему здесь есть `calendar_limit`:
        - у владельца может быть очень много подписанных календарей;
        - полный AppleScript-обход всех источников на macOS легко уходит в
          десятки секунд и делает команду практически непригодной;
        - для owner-команды полезнее быстрый truthful snapshot по первым
          календарям, чем "идеально полный" ответ, который зависает.
        """
        script = r'''
on run argv
    set maxItems to (item 1 of argv as integer)
    set daysAhead to (item 2 of argv as integer)
    set maxCalendars to (item 3 of argv as integer)
    set scannedCalendars to 0
    set outLines to {}
    tell application "Calendar"
        set startWindow to current date
        set endWindow to startWindow + (daysAhead * days)
        repeat with calRef in every calendar
            set scannedCalendars to scannedCalendars + 1
            try
                set evs to (every event of calRef whose start date ≥ startWindow and start date ≤ endWindow)
                repeat with ev in evs
                    set eventLabel to summary of ev as text
                    set startValue to start date of ev
                    set startLabel to ((date string of startValue) & " " & (time string of startValue))
                    set end of outLines to ((name of calRef as text) & "||" & eventLabel & "||" & startLabel)
                    if (count of outLines) is greater than or equal to maxItems then exit repeat
                end repeat
            end try
            if (count of outLines) is greater than or equal to maxItems then exit repeat
            if scannedCalendars is greater than or equal to maxCalendars then exit repeat
        end repeat
    end tell
    set AppleScript's text item delimiters to linefeed
    return outLines as text
end run
'''
        output = await self._run_osascript(
            script,
            argv=[
                str(max(1, int(limit or 1))),
                str(max(1, int(days_ahead or 1))),
                str(max(1, int(calendar_limit or 1))),
            ],
            timeout_sec=max(self.timeout_sec, 25.0),
        )
        rows: list[dict[str, str]] = []
        for line in output.splitlines():
            if not line.strip():
                continue
            calendar_name, _, tail = line.partition("||")
            title, _, start_label = tail.partition("||")
            rows.append(
                {
                    "calendar_name": calendar_name.strip(),
                    "title": title.strip(),
                    "start_label": start_label.strip(),
                }
            )
        return rows

    async def create_calendar_event(
        self,
        *,
        title: str,
        start_at: datetime,
        duration_minutes: int = 30,
        calendar_name: str = "",
    ) -> dict[str, str]:
        """Создаёт событие в Calendar и возвращает uid/календарь."""
        event_title = str(title or "").strip()
        if not event_title:
            raise ValueError("empty_event_title")
        duration = max(1, int(duration_minutes or 30))
        end_at = start_at + timedelta(minutes=duration)
        script = r'''
on run argv
    set calendarNameArg to item 1 of argv
    set eventTitle to item 2 of argv
    set sy to (item 3 of argv as integer)
    set sm to (item 4 of argv as integer)
    set sd to (item 5 of argv as integer)
    set sh to (item 6 of argv as integer)
    set smin to (item 7 of argv as integer)
    set ss to (item 8 of argv as integer)
    set ey to (item 9 of argv as integer)
    set em to (item 10 of argv as integer)
    set ed to (item 11 of argv as integer)
    set eh to (item 12 of argv as integer)
    set emin to (item 13 of argv as integer)
    set es to (item 14 of argv as integer)
    set monthNames to {January, February, March, April, May, June, July, August, September, October, November, December}

    set startValue to current date
    set year of startValue to sy
    set month of startValue to item sm of monthNames
    set day of startValue to sd
    set time of startValue to ((sh * hours) + (smin * minutes) + ss)

    set endValue to current date
    set year of endValue to ey
    set month of endValue to item em of monthNames
    set day of endValue to ed
    set time of endValue to ((eh * hours) + (emin * minutes) + es)

    tell application "Calendar"
        if calendarNameArg is "" then
            set targetCalendar to first calendar whose writable is true
        else
            set targetCalendar to first calendar whose name is calendarNameArg
        end if
        tell targetCalendar
            set newEvent to make new event with properties {summary:eventTitle, start date:startValue, end date:endValue}
            return ((uid of newEvent as text) & linefeed & (name of targetCalendar as text))
        end tell
    end tell
end run
'''
        argv = [calendar_name.strip(), event_title]
        argv.extend(self._datetime_argv(start_at))
        argv.extend(self._datetime_argv(end_at))
        output = await self._run_osascript(
            script,
            argv=argv,
            timeout_sec=max(self.timeout_sec, 20.0),
        )
        event_id, _, resolved_calendar = output.partition("\n")
        return {"id": event_id.strip(), "calendar_name": resolved_calendar.strip()}

    async def status(self, *, apps_limit: int = 8) -> dict[str, Any]:
        """
        Возвращает мягкий status macOS automation без падения всего ответа.

        Это полезно для owner-команды `!mac status`: даже если, например,
        Accessibility permission ещё не выдан, clipboard и open-контур можно
        показать отдельно, а не ронять весь статус целиком.
        """
        payload: dict[str, Any] = {
            "available": self.is_available(),
            "platform": platform.platform(),
            "frontmost_app": "",
            "frontmost_window": "",
            "running_apps": [],
            "clipboard_chars": 0,
            "clipboard_preview": "",
            "reminder_lists": [],
            "note_folders": [],
            "calendars": [],
            "warnings": [],
        }
        if not payload["available"]:
            payload["warnings"].append("macos_automation_unavailable")
            return payload

        try:
            front = await self.get_frontmost_app()
            payload["frontmost_app"] = front.get("app_name", "")
            payload["frontmost_window"] = front.get("window_title", "")
        except Exception as exc:  # noqa: BLE001 - статус должен деградировать мягко
            logger.warning("macos_frontmost_status_failed", error=str(exc))
            payload["warnings"].append(f"frontmost:{exc}")

        try:
            payload["running_apps"] = await self.list_running_apps(limit=apps_limit)
        except Exception as exc:  # noqa: BLE001
            logger.warning("macos_running_apps_status_failed", error=str(exc))
            payload["warnings"].append(f"running_apps:{exc}")

        try:
            clipboard_text = await self.get_clipboard_text()
            payload["clipboard_chars"] = len(clipboard_text)
            payload["clipboard_preview"] = self._preview_text(clipboard_text, limit=100)
        except Exception as exc:  # noqa: BLE001
            logger.warning("macos_clipboard_status_failed", error=str(exc))
            payload["warnings"].append(f"clipboard:{exc}")

        try:
            payload["reminder_lists"] = await self.list_reminder_lists()
        except Exception as exc:  # noqa: BLE001
            logger.warning("macos_reminders_status_failed", error=str(exc))
            payload["warnings"].append(f"reminders:{exc}")

        try:
            payload["note_folders"] = await self.list_note_folders()
        except Exception as exc:  # noqa: BLE001
            logger.warning("macos_notes_status_failed", error=str(exc))
            payload["warnings"].append(f"notes:{exc}")

        try:
            payload["calendars"] = await self.list_calendars()
        except Exception as exc:  # noqa: BLE001
            logger.warning("macos_calendars_status_failed", error=str(exc))
            payload["warnings"].append(f"calendars:{exc}")

        return payload


macos_automation = MacOSAutomationService()

__all__ = ["MacOSAutomationError", "MacOSAutomationService", "macos_automation"]
