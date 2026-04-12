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
import datetime
import os
import platform
import shutil
import subprocess
from typing import Any

import structlog

from ..core.subprocess_env import clean_subprocess_env

logger = structlog.get_logger(__name__)


class MacOSAutomationError(Exception):
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

    def __init__(self, timeout_sec: float = 30.0) -> None:
        self.timeout_sec = timeout_sec

    @staticmethod
    def is_available() -> bool:
        """Проверяет, есть ли в текущем runtime базовые macOS-инструменты."""
        if platform.system() != "Darwin":
            return False
        return all(shutil.which(tool) for tool in ("osascript", "open", "pbcopy", "pbpaste"))

    async def _run_command(self, args: list[str]) -> str:
        """Запускает локальную команду и возвращает stdout."""
        if not args:
            raise MacOSAutomationError("empty_command")
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=clean_subprocess_env(),
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=self.timeout_sec + 1.0
                )
            except asyncio.TimeoutError:
                proc.kill()
                raise MacOSAutomationError(f"timeout: {' '.join(str(a) for a in args[:2])}")
            if proc.returncode != 0:
                err = (stderr or b"").decode("utf-8", "replace").strip()
                raise MacOSAutomationError(f"command_failed: {err}")
            return (stdout or b"").decode("utf-8", "replace").strip()
        except MacOSAutomationError:
            raise
        except Exception as exc:
            raise MacOSAutomationError(f"command_failed: {exc}") from exc

    async def _run_osascript(
        self, script: str, *argv: str, timeout_sec: float | None = None
    ) -> str:
        """Запускает AppleScript через `osascript` с аргументами."""
        cmd = ["osascript", "-e", script, *argv]
        orig = self.timeout_sec
        if timeout_sec is not None:
            self.timeout_sec = timeout_sec
        try:
            return await self._run_command(cmd)
        finally:
            self.timeout_sec = orig

    @staticmethod
    def _datetime_argv(value: datetime.datetime) -> list[str]:
        """Сериализует datetime в argv для AppleScript без locale-парсинга."""
        dt = value
        return [
            str(dt.year),
            str(dt.month),
            str(dt.day),
            str(dt.hour),
            str(dt.minute),
            str(dt.second),
        ]

    @staticmethod
    def _preview_text(text: str, limit: int) -> str:
        """Возвращает компактный preview текста для статусов и ответов."""
        raw = text.replace("\r", " ").replace("\n", " ")
        if limit > 0 and len(raw) > limit:
            return raw[: limit - 1] + "\u2026"
        return raw

    @staticmethod
    def _resolve_path_or_url(target: str) -> dict[str, str]:
        """
        Нормализует пользовательскую цель для `open`.

        Поддерживаем:
        - `http/https/mailto/tel/file` URL;
        - абсолютные пути;
        - `~/...` и относительные пути от текущего рабочего каталога runtime.
        """
        if not target:
            raise MacOSAutomationError("empty_target")
        scheme = target.split(":", 1)[0].lower() if ":" in target else ""
        if scheme in frozenset({"http", "https", "mailto", "tel", "file"}):
            return {"kind": "url", "target": target}
        expanded = os.path.expanduser(target)
        if not os.path.isabs(expanded):
            expanded = os.path.abspath(expanded)
        if not os.path.exists(expanded):
            raise MacOSAutomationError("Путь не найден: " + target)
        return {"kind": "path", "target": expanded}

    async def get_frontmost_app(self) -> dict[str, str]:
        """Возвращает активное приложение и заголовок переднего окна."""
        script = (
            '\ntell application "System Events"\n'
            "    set frontApp to first application process whose frontmost is true\n"
            "    set appName to name of frontApp\n"
            '    set windowTitle to ""\n'
            "    try\n"
            "        set windowTitle to name of front window of frontApp\n"
            "    end try\n"
            "    return appName & linefeed & windowTitle\n"
            "end tell\n"
        )
        output = await self._run_osascript(script)
        parts = output.split("\n", 1)
        return {
            "app_name": parts[0],
            "window_title": parts[1] if len(parts) > 1 else "",
        }

    async def list_running_apps(self, limit: int = 0) -> list[str]:
        """Возвращает список видимых пользовательских приложений."""
        script = (
            '\ntell application "System Events"\n'
            "    set appNames to name of every application process whose background only is false\n"
            "    set AppleScript's text item delimiters to linefeed\n"
            "    return appNames as text\n"
            "end tell\n"
        )
        output = await self._run_osascript(script)
        names = [line.strip() for line in output.splitlines() if line.strip()]
        if limit > 0:
            names = names[:limit]
        return names

    async def get_clipboard_text(self) -> str:
        """Читает текстовый clipboard через `pbpaste`."""
        return await self._run_command(["pbpaste"])

    async def set_clipboard_text(self, text: str) -> str:
        """Записывает текст в clipboard через `pbcopy`."""
        proc = await asyncio.create_subprocess_exec(
            "pbcopy",
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=clean_subprocess_env(),
        )
        await proc.communicate(input=text.encode("utf-8"))
        return ""

    async def show_notification(self, *, title: str, message: str, subtitle: str = "") -> None:
        """Показывает локальное системное уведомление macOS."""
        script = (
            "\non run argv\n"
            "    set theTitle to item 1 of argv\n"
            "    set theBody to item 2 of argv\n"
            '    set theSubtitle to ""\n'
            "    if (count of argv) > 2 then\n"
            "        set theSubtitle to item 3 of argv\n"
            "    end if\n"
            '    if theSubtitle is "" then\n'
            "        display notification theBody with title theTitle\n"
            "    else\n"
            "        display notification theBody with title theTitle subtitle theSubtitle\n"
            "    end if\n"
            '    return "ok"\n'
            "end run\n"
        )
        argv: list[str] = [title or "\u041a\u0440\u0430\u0431", message]
        if subtitle:
            argv.append(subtitle)
        await self._run_osascript(script, *argv)

    async def open_app(self, app_name: str) -> dict[str, str]:
        """Открывает приложение через `open -a`."""
        if not app_name:
            raise MacOSAutomationError("empty_app_name")
        await self._run_command(["open", "-a", app_name])
        return {"app_name": app_name}

    async def open_target(self, target: str) -> dict[str, str]:
        """Открывает URL или путь штатным `open`."""
        resolved = self._resolve_path_or_url(target)
        await self._run_command(["open", resolved["target"]])
        return {"kind": resolved["kind"], "target": resolved["target"]}

    async def reveal_in_finder(self, target: str) -> dict[str, str]:
        """Показывает файл/папку в Finder через `open -R`."""
        resolved = self._resolve_path_or_url(target)
        if resolved["kind"] != "path":
            raise MacOSAutomationError("finder_reveal_requires_path")
        await self._run_command(["open", "-R", resolved["target"]])
        return {"path": resolved["target"]}

    async def list_reminder_lists(self) -> list[str]:
        """Возвращает имена списков Reminders."""
        script = (
            '\ntell application "Reminders"\n'
            "    set listNames to name of every list\n"
            "    set AppleScript's text item delimiters to linefeed\n"
            "    return listNames as text\n"
            "end tell\n"
        )
        output = await self._run_osascript(script)
        return [line.strip() for line in output.splitlines() if line.strip()]

    async def list_reminders(self, limit: int = 10) -> list[dict[str, str]]:
        """Возвращает несколько ближайших незавершённых reminder-элементов."""
        script = (
            "\non run argv\n"
            "    set maxItems to (item 1 of argv as integer)\n"
            "    set outLines to {}\n"
            '    tell application "Reminders"\n'
            "        repeat with targetList in every list\n"
            "            repeat with reminderRef in reminders of targetList\n"
            "                try\n"
            "                    if completed of reminderRef is false then\n"
            "                        set reminderName to name of reminderRef as text\n"
            '                        set dueLabel to ""\n'
            "                        try\n"
            "                            set dueValue to due date of reminderRef\n"
            "                            if dueValue is not missing value then\n"
            '                                set dueLabel to ((date string of dueValue) & " " & (time string of dueValue))\n'
            "                            end if\n"
            "                        end try\n"
            '                        set end of outLines to ((name of targetList as text) & "||" & reminderName & "||" & dueLabel)\n'
            "                        if (count of outLines) is greater than or equal to maxItems then exit repeat\n"
            "                    end if\n"
            "                end try\n"
            "            end repeat\n"
            "            if (count of outLines) is greater than or equal to maxItems then exit repeat\n"
            "        end repeat\n"
            "    end tell\n"
            "    set AppleScript's text item delimiters to linefeed\n"
            "    return outLines as text\n"
            "end run\n"
        )
        output = await self._run_osascript(script, str(limit))
        rows: list[dict[str, str]] = []
        for line in output.splitlines():
            parts = line.split("||", 2)
            if len(parts) == 3:
                list_name, title, due_label = parts
                rows.append({"list_name": list_name, "title": title, "due_label": due_label})
        return rows

    async def create_reminder(
        self,
        *,
        title: str,
        due_at: datetime.datetime | None = None,
        list_name: str = "",
    ) -> dict[str, str]:
        """Создаёт reminder в Reminders и возвращает его id/список."""
        if not title:
            raise MacOSAutomationError("empty_reminder_title")
        script = (
            "\non run argv\n"
            "    set listNameArg to item 1 of argv\n"
            "    set reminderTitle to item 2 of argv\n"
            '    tell application "Reminders"\n'
            '        if listNameArg is "" then\n'
            "            set targetList to first list\n"
            "        else\n"
            "            set targetList to first list whose name is listNameArg\n"
            "        end if\n"
            "        set newReminder to make new reminder at end of reminders of targetList with properties {name:reminderTitle}\n"
            "        if (count of argv) is greater than 2 then\n"
            "            set y to (item 3 of argv as integer)\n"
            "            set m to (item 4 of argv as integer)\n"
            "            set d to (item 5 of argv as integer)\n"
            "            set hh to (item 6 of argv as integer)\n"
            "            set mm to (item 7 of argv as integer)\n"
            "            set ss to (item 8 of argv as integer)\n"
            "            set monthNames to {January, February, March, April, May, June, July, August, September, October, November, December}\n"
            "            set dueValue to current date\n"
            "            set year of dueValue to y\n"
            "            set month of dueValue to item m of monthNames\n"
            "            set day of dueValue to d\n"
            "            set time of dueValue to ((hh * hours) + (mm * minutes) + ss)\n"
            "            set due date of newReminder to dueValue\n"
            "        end if\n"
            "        return ((id of newReminder as text) & linefeed & (name of targetList as text))\n"
            "    end tell\n"
            "end run\n"
        )
        reminder_title = title
        argv: list[str] = [list_name, reminder_title]
        if due_at is not None:
            argv.extend(self._datetime_argv(due_at))
        output = await self._run_osascript(script, *argv)
        parts = output.split("\n", 1)
        reminder_id = parts[0]
        resolved_list = parts[1] if len(parts) > 1 else list_name
        return {"id": reminder_id, "list_name": resolved_list}

    async def list_note_folders(self) -> list[str]:
        """Возвращает имена папок default account в Notes."""
        script = (
            '\ntell application "Notes"\n'
            "    tell default account\n"
            "        set folderNames to name of every folder\n"
            "        set AppleScript's text item delimiters to linefeed\n"
            "        return folderNames as text\n"
            "    end tell\n"
            "end tell\n"
        )
        output = await self._run_osascript(script)
        return [line.strip() for line in output.splitlines() if line.strip()]

    async def list_notes(self, limit: int = 20) -> list[dict[str, str]]:
        """Возвращает несколько заметок с аккаунтом/папкой/заголовком."""
        script = (
            "\non run argv\n"
            "    set maxItems to (item 1 of argv as integer)\n"
            "    set outLines to {}\n"
            '    tell application "Notes"\n'
            "        repeat with acc in accounts\n"
            "            repeat with folderRef in folders of acc\n"
            "                repeat with noteRef in notes of folderRef\n"
            '                    set end of outLines to ((name of acc as text) & "||" & (name of folderRef as text) & "||" & (name of noteRef as text))\n'
            "                    if (count of outLines) is greater than or equal to maxItems then exit repeat\n"
            "                end repeat\n"
            "                if (count of outLines) is greater than or equal to maxItems then exit repeat\n"
            "            end repeat\n"
            "            if (count of outLines) is greater than or equal to maxItems then exit repeat\n"
            "        end repeat\n"
            "    end tell\n"
            "    set AppleScript's text item delimiters to linefeed\n"
            "    return outLines as text\n"
            "end run\n"
        )
        output = await self._run_osascript(script, str(limit))
        rows: list[dict[str, str]] = []
        for line in output.splitlines():
            parts = line.split("||", 2)
            if len(parts) == 3:
                account_name, folder_name, note_title = parts
                rows.append(
                    {"account_name": account_name, "folder_name": folder_name, "title": note_title}
                )
        return rows

    async def create_note(
        self,
        *,
        title: str,
        body: str,
        folder_name: str = "",
    ) -> dict[str, str]:
        """Создаёт заметку в Notes и возвращает её id/папку."""
        if not title:
            raise MacOSAutomationError("empty_note_title")
        if not body:
            raise MacOSAutomationError("empty_note_body")
        script = (
            "\non run argv\n"
            "    set folderNameArg to item 1 of argv\n"
            "    set noteTitle to item 2 of argv\n"
            "    set noteBody to item 3 of argv\n"
            '    tell application "Notes"\n'
            "        tell default account\n"
            '            if folderNameArg is "" then\n'
            "                set targetFolder to first folder\n"
            "            else\n"
            "                set targetFolder to first folder whose name is folderNameArg\n"
            "            end if\n"
            "            set newNote to make new note at targetFolder with properties {name:noteTitle, body:noteBody}\n"
            "            return ((id of newNote as text) & linefeed & (name of targetFolder as text))\n"
            "        end tell\n"
            "    end tell\n"
            "end run\n"
        )
        note_title = title
        note_body = body
        output = await self._run_osascript(script, folder_name, note_title, note_body)
        parts = output.split("\n", 1)
        note_id = parts[0]
        resolved_folder = parts[1] if len(parts) > 1 else folder_name
        return {"id": note_id, "folder_name": resolved_folder}

    async def list_calendars(self) -> list[str]:
        """Возвращает список календарей из приложения Calendar."""
        script = (
            '\ntell application "Calendar"\n'
            "    set calNames to name of every calendar\n"
            "    set AppleScript's text item delimiters to linefeed\n"
            "    return calNames as text\n"
            "end tell\n"
        )
        output = await self._run_osascript(script)
        return [line.strip() for line in output.splitlines() if line.strip()]

    async def list_upcoming_calendar_events(
        self,
        limit: int = 10,
        days_ahead: int = 7,
        calendar_limit: int = 25,
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
        script = (
            "\non run argv\n"
            "    set maxItems to (item 1 of argv as integer)\n"
            "    set daysAhead to (item 2 of argv as integer)\n"
            "    set maxCalendars to (item 3 of argv as integer)\n"
            "    set scannedCalendars to 0\n"
            "    set outLines to {}\n"
            '    tell application "Calendar"\n'
            "        set startWindow to current date\n"
            "        set endWindow to startWindow + (daysAhead * days)\n"
            "        repeat with calRef in every calendar\n"
            "            set scannedCalendars to scannedCalendars + 1\n"
            "            try\n"
            "                set evs to (every event of calRef whose start date \u2265 startWindow and start date \u2264 endWindow)\n"
            "                repeat with ev in evs\n"
            "                    set eventLabel to summary of ev as text\n"
            "                    set startValue to start date of ev\n"
            '                    set startLabel to ((date string of startValue) & " " & (time string of startValue))\n'
            '                    set end of outLines to ((name of calRef as text) & "||" & eventLabel & "||" & startLabel)\n'
            "                    if (count of outLines) is greater than or equal to maxItems then exit repeat\n"
            "                end repeat\n"
            "            end try\n"
            "            if (count of outLines) is greater than or equal to maxItems then exit repeat\n"
            "            if scannedCalendars is greater than or equal to maxCalendars then exit repeat\n"
            "        end repeat\n"
            "    end tell\n"
            "    set AppleScript's text item delimiters to linefeed\n"
            "    return outLines as text\n"
            "end run\n"
        )
        output = await self._run_osascript(
            script, str(limit), str(days_ahead), str(calendar_limit), timeout_sec=25.0
        )
        rows: list[dict[str, str]] = []
        for line in output.splitlines():
            parts = line.split("||", 2)
            if len(parts) == 3:
                calendar_name, title, start_label = parts
                rows.append(
                    {"calendar_name": calendar_name, "title": title, "start_label": start_label}
                )
        return rows

    async def create_calendar_event(
        self,
        *,
        title: str,
        start_at: datetime.datetime,
        duration_minutes: int = 30,
        calendar_name: str = "",
    ) -> dict[str, str]:
        """Создаёт событие в Calendar и возвращает uid/календарь."""
        if not title:
            raise MacOSAutomationError("empty_event_title")
        duration = datetime.timedelta(minutes=duration_minutes)
        end_at = start_at + duration
        script = (
            "\non run argv\n"
            "    set calendarNameArg to item 1 of argv\n"
            "    set eventTitle to item 2 of argv\n"
            "    set sy to (item 3 of argv as integer)\n"
            "    set sm to (item 4 of argv as integer)\n"
            "    set sd to (item 5 of argv as integer)\n"
            "    set sh to (item 6 of argv as integer)\n"
            "    set smin to (item 7 of argv as integer)\n"
            "    set ss to (item 8 of argv as integer)\n"
            "    set ey to (item 9 of argv as integer)\n"
            "    set em to (item 10 of argv as integer)\n"
            "    set ed to (item 11 of argv as integer)\n"
            "    set eh to (item 12 of argv as integer)\n"
            "    set emin to (item 13 of argv as integer)\n"
            "    set es to (item 14 of argv as integer)\n"
            "    set monthNames to {January, February, March, April, May, June, July, August, September, October, November, December}\n"
            "\n"
            "    set startValue to current date\n"
            "    set year of startValue to sy\n"
            "    set month of startValue to item sm of monthNames\n"
            "    set day of startValue to sd\n"
            "    set time of startValue to ((sh * hours) + (smin * minutes) + ss)\n"
            "\n"
            "    set endValue to current date\n"
            "    set year of endValue to ey\n"
            "    set month of endValue to item em of monthNames\n"
            "    set day of endValue to ed\n"
            "    set time of endValue to ((eh * hours) + (emin * minutes) + es)\n"
            "\n"
            '    tell application "Calendar"\n'
            '        if calendarNameArg is "" then\n'
            "            set targetCalendar to first calendar whose writable is true\n"
            "        else\n"
            "            set targetCalendar to first calendar whose name is calendarNameArg\n"
            "        end if\n"
            "        tell targetCalendar\n"
            "            set newEvent to make new event with properties {summary:eventTitle, start date:startValue, end date:endValue}\n"
            "            return ((uid of newEvent as text) & linefeed & (name of targetCalendar as text))\n"
            "        end tell\n"
            "    end tell\n"
            "end run\n"
        )
        event_title = title
        argv: list[str] = [
            calendar_name,
            event_title,
            *self._datetime_argv(start_at),
            *self._datetime_argv(end_at),
        ]
        output = await self._run_osascript(script, *argv, timeout_sec=20.0)
        parts = output.split("\n", 1)
        event_id = parts[0]
        resolved_calendar = parts[1] if len(parts) > 1 else calendar_name
        return {"id": event_id, "calendar_name": resolved_calendar}

    async def focus_app(self, app_name: str) -> dict[str, str]:
        """Выводит приложение на передний план через AppleScript activate.

        Требует Accessibility permission в macOS (Системные настройки → Конфиденциальность → Accessibility).
        """
        if not app_name or not app_name.strip():
            raise MacOSAutomationError("empty_app_name")
        name = app_name.strip()
        script = f'\ntell application "{name}" to activate\nreturn "ok"\n'
        await self._run_osascript(script)
        return {"app_name": name, "action": "focused"}

    async def type_text(self, text: str, *, app_name: str | None = None) -> dict[str, str]:
        """Вводит текст через keystroke System Events (имитация клавиатуры).

        Если app_name передан — сначала активирует приложение.
        Требует Accessibility permission.
        Внимание: работает с латиницей и спецсимволами; для кириллицы используй clipboard workaround.
        """
        if not text:
            raise MacOSAutomationError("empty_text")
        if app_name:
            await self.focus_app(app_name)
            # Небольшая пауза после активации чтобы окно успело стать foreground
            script = (
                '\ntell application "System Events"\n'
                f'    keystroke "{text}"\n'
                "end tell\n"
                'return "ok"\n'
            )
        else:
            script = (
                '\ntell application "System Events"\n'
                f'    keystroke "{text}"\n'
                "end tell\n"
                'return "ok"\n'
            )
        await self._run_osascript(script)
        return {"text_length": str(len(text)), "app_name": app_name or "frontmost"}

    async def type_text_via_clipboard(
        self, text: str, *, app_name: str | None = None
    ) -> dict[str, str]:
        """Вводит текст через clipboard + Cmd+V (работает с кириллицей и Unicode).

        Алгоритм: записывает text в clipboard → активирует приложение (опционально) → Cmd+V.
        Предыдущее содержимое clipboard перезаписывается.
        """
        if not text:
            raise MacOSAutomationError("empty_text")
        await self.set_clipboard_text(text)
        if app_name:
            await self.focus_app(app_name)
        script = (
            '\ntell application "System Events"\n'
            '    keystroke "v" using {command down}\n'
            "end tell\n"
            'return "ok"\n'
        )
        await self._run_osascript(script)
        return {
            "text_length": str(len(text)),
            "app_name": app_name or "frontmost",
            "method": "clipboard_paste",
        }

    async def click_ui_element(
        self,
        app_name: str,
        element_name: str,
        *,
        element_type: str = "button",
        window_index: int = 1,
    ) -> dict[str, str]:
        """Нажимает кнопку/элемент UI через System Events Accessibility API.

        Требует Accessibility permission.
        Параметры:
          app_name:     имя процесса приложения (например "Safari", "Finder")
          element_name: заголовок/имя элемента (например "OK", "Отмена")
          element_type: тип элемента ("button" по умолчанию; также "menu item", "checkbox")
          window_index: номер окна (1-based, по умолчанию переднее окно)
        """
        if not app_name or not element_name:
            raise MacOSAutomationError("empty_app_or_element")
        app = app_name.strip()
        elem = element_name.strip()
        etype = element_type.strip() or "button"
        script = (
            '\ntell application "System Events"\n'
            f'    tell process "{app}"\n'
            f'        click {etype} "{elem}" of window {window_index}\n'
            "    end tell\n"
            "end tell\n"
            'return "ok"\n'
        )
        await self._run_osascript(script)
        return {"app_name": app, "element": elem, "type": etype, "window": str(window_index)}

    async def press_key(self, key: str, *, modifiers: list[str] | None = None) -> dict[str, str]:
        """Нажимает клавишу (возможно с модификаторами) через System Events.

        Примеры:
          press_key("return")
          press_key("tab")
          press_key("a", modifiers=["command"])
          press_key("z", modifiers=["command", "shift"])
        Требует Accessibility permission.
        """
        if not key:
            raise MacOSAutomationError("empty_key")
        mods = modifiers or []
        if mods:
            mod_str = "{" + ", ".join(f"{m} down" for m in mods) + "}"
            script = (
                '\ntell application "System Events"\n'
                f'    key code (key code of "{key}") using {mod_str}\n'
                "end tell\n"
                'return "ok"\n'
            )
            # key code lookup не всегда работает — используем keystroke для простых случаев
            # и key code для навигационных клавиш
            nav_keys = {
                "return",
                "tab",
                "escape",
                "space",
                "delete",
                "backspace",
                "up",
                "down",
                "left",
                "right",
                "home",
                "end",
                "pageup",
                "pagedown",
            }
            if key.lower() in nav_keys:
                key_map = {
                    "return": "13",
                    "tab": "48",
                    "escape": "53",
                    "space": "49",
                    "delete": "51",
                    "backspace": "51",
                    "up": "126",
                    "down": "125",
                    "left": "123",
                    "right": "124",
                    "home": "115",
                    "end": "119",
                    "pageup": "116",
                    "pagedown": "121",
                }
                code = key_map.get(key.lower(), "")
                if code:
                    script = (
                        '\ntell application "System Events"\n'
                        f"    key code {code} using {mod_str}\n"
                        "end tell\n"
                        'return "ok"\n'
                    )
        else:
            script = (
                '\ntell application "System Events"\n'
                f'    keystroke "{key}"\n'
                "end tell\n"
                'return "ok"\n'
            )
        await self._run_osascript(script)
        return {"key": key, "modifiers": mods}

    @staticmethod
    def is_ocr_available() -> bool:
        """True если tesseract установлен (brew install tesseract)."""
        return bool(shutil.which("tesseract"))

    async def ocr_image(self, image_bytes: bytes, *, lang: str = "") -> str:
        """Извлекает текст из изображения через tesseract OCR CLI.

        Требует: brew install tesseract
        Поддерживает PNG, JPEG и другие форматы Pillow/tesseract.
        Параметры:
          image_bytes: байты изображения (PNG от browser_bridge.screenshot())
          lang:        язык OCR (например "rus", "eng", "rus+eng"); по умолчанию auto
        """
        if not self.is_ocr_available():
            raise MacOSAutomationError(
                "tesseract_not_installed: установи через 'brew install tesseract' "
                "и 'brew install tesseract-lang' для русского языка"
            )
        import os
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(image_bytes)
            tmp_path = f.name
        try:
            args = ["tesseract", tmp_path, "stdout", "--psm", "3"]
            if lang:
                args += ["-l", lang]
            text = await asyncio.wait_for(
                self._run_command(args),
                timeout=30.0,
            )
            return text.strip()
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    async def status(self) -> dict[str, Any]:
        """
        Возвращает мягкий status macOS automation без падения всего ответа.

        Это полезно для owner-команды `!mac status`: даже если, например,
        Accessibility permission ещё не выдан, clipboard и open-контур можно
        показать отдельно, а не ронять весь статус целиком.
        """
        result: dict[str, Any] = {
            "available": False,
            "platform": platform.system(),
            "frontmost_app": None,
            "frontmost_window": None,
            "running_apps": [],
            "clipboard_chars": 0,
            "clipboard_preview": "",
            "reminder_lists": [],
            "note_folders": [],
            "calendars": [],
            "warnings": [],
        }
        if not self.is_available():
            result["warnings"].append("macos_automation_unavailable")
            return result
        result["available"] = True

        try:
            fa = await self.get_frontmost_app()
            result["frontmost_app"] = fa.get("app_name")
            result["frontmost_window"] = fa.get("window_title")
        except Exception as exc:
            result["warnings"].append("macos_frontmost_status_failed")
            logger.debug("frontmost:", error=str(exc))

        try:
            result["running_apps"] = await self.list_running_apps(limit=20)
        except Exception as exc:
            result["warnings"].append("macos_running_apps_status_failed")
            logger.debug("running_apps:", error=str(exc))

        try:
            cb = await self.get_clipboard_text()
            result["clipboard_chars"] = len(cb)
            result["clipboard_preview"] = self._preview_text(cb, 100)
        except Exception as exc:
            result["warnings"].append("macos_clipboard_status_failed")
            logger.debug("clipboard:", error=str(exc))

        try:
            result["reminder_lists"] = await self.list_reminder_lists()
        except Exception as exc:
            result["warnings"].append("macos_reminders_status_failed")
            logger.debug("reminders:", error=str(exc))

        try:
            result["note_folders"] = await self.list_note_folders()
        except Exception as exc:
            result["warnings"].append("macos_notes_status_failed")
            logger.debug("notes:", error=str(exc))

        try:
            result["calendars"] = await self.list_calendars()
        except Exception as exc:
            result["warnings"].append("macos_calendars_status_failed")
            logger.debug("calendars:", error=str(exc))

        return result

    async def health_check(self) -> dict:
        """Возвращает probe-результат для build_system_control_snapshot().

        Формат: {"ok": bool, "blocked": bool, "error": str, "tools": list[str]}
        - ok=True:      osascript + pbcopy/pbpaste доступны, базовый осascript работает
        - blocked=True: macOS недоступна или инструменты не установлены
        """
        if not self.is_available():
            missing = [t for t in ("osascript", "open", "pbcopy", "pbpaste") if not shutil.which(t)]
            return {
                "ok": False,
                "blocked": True,
                "error": f"unavailable on {platform.system()}"
                if platform.system() != "Darwin"
                else f"missing tools: {missing}",
                "tools": [],
            }
        # Быстрая проверка — запускаем минимальный osascript без side-effects
        try:
            await asyncio.wait_for(
                self._run_osascript('return "ok"'),
                timeout=3.0,
            )
        except asyncio.TimeoutError:
            return {"ok": False, "blocked": False, "error": "osascript timeout 3s", "tools": []}
        except Exception as exc:
            return {"ok": False, "blocked": False, "error": repr(exc), "tools": []}

        available_tools = [t for t in ("osascript", "open", "pbcopy", "pbpaste") if shutil.which(t)]
        return {
            "ok": True,
            "blocked": False,
            "error": "",
            "tools": available_tools,
            "ocr_available": self.is_ocr_available(),
        }


# Глобальный синглтон для импорта в хендлерах
macos_automation = MacOSAutomationService()
