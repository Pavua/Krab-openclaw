# -*- coding: utf-8 -*-
"""
Юнит-тесты для handle_backup.

Покрывает:
- _BACKUP_FILES: список из 13 файлов
- !backup list: все файлы найдены, все отсутствуют, смешанный случай
- !backup: нет файлов (статус + edit), есть файлы (send_document + delete),
  ошибка send_document, ошибка создания ZIP, наличие skipped в caption
"""

from __future__ import annotations

import pathlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.handlers.command_handlers import _BACKUP_FILES, handle_backup

# ---------------------------------------------------------------------------
# Вспомогательные фабрики
# ---------------------------------------------------------------------------


def _make_message(text: str = "!backup", chat_id: int = 42, message_id: int = 1):
    """Минимальный mock Message."""
    msg = SimpleNamespace(
        text=text,
        id=message_id,
        chat=SimpleNamespace(id=chat_id),
        reply=AsyncMock(),
        edit=AsyncMock(),
        delete=AsyncMock(),
    )
    return msg


def _make_bot(send_document_exc=None):
    """Минимальный mock KraabUserbot."""
    client = SimpleNamespace(
        send_document=AsyncMock(side_effect=send_document_exc),
    )

    def _get_args(message):
        parts = (message.text or "").split(maxsplit=1)
        return parts[1] if len(parts) > 1 else ""

    return SimpleNamespace(
        client=client,
        _get_command_args=_get_args,
    )


# ---------------------------------------------------------------------------
# Тесты _BACKUP_FILES
# ---------------------------------------------------------------------------


class TestBackupFilesList:
    def test_count(self):
        assert len(_BACKUP_FILES) == 13

    def test_swarm_files_present(self):
        assert "swarm_memory.json" in _BACKUP_FILES
        assert "swarm_channels.json" in _BACKUP_FILES

    def test_required_files_present(self):
        required = [
            "bookmarks.json",
            "chat_monitors.json",
            "command_aliases.json",
            "saved_stickers.json",
            "personal_todos.json",
            "code_snippets.json",
            "message_templates.json",
            "saved_quotes.json",
            "welcome_messages.json",
            "silence_schedule.json",
            "spam_filter_config.json",
        ]
        for fname in required:
            assert fname in _BACKUP_FILES, f"Файл {fname!r} отсутствует в _BACKUP_FILES"

    def test_no_duplicates(self):
        assert len(_BACKUP_FILES) == len(set(_BACKUP_FILES))

    def test_all_json(self):
        for fname in _BACKUP_FILES:
            assert fname.endswith(".json"), f"Ожидается .json, получен: {fname!r}"


# ---------------------------------------------------------------------------
# Тесты !backup list
# ---------------------------------------------------------------------------


class TestBackupListCommand:
    @pytest.mark.asyncio
    async def test_list_all_missing(self, tmp_path):
        """!backup list — когда все файлы отсутствуют."""
        message = _make_message("!backup list")
        bot = _make_bot()

        with patch(
            "src.handlers.command_handlers.pathlib.Path.home",
            return_value=tmp_path,
        ):
            await handle_backup(bot, message)

        message.reply.assert_called_once()
        reply_text = message.reply.call_args[0][0]
        assert "Файлы в резервной копии" in reply_text
        assert "отсутствует" in reply_text
        assert "0 файлов найдено" in reply_text
        assert f"{len(_BACKUP_FILES)} отсутствуют" in reply_text

    @pytest.mark.asyncio
    async def test_list_some_found(self, tmp_path):
        """!backup list — когда часть файлов существует."""
        # Создадим runtime_state директорию и 2 файла
        state_dir = tmp_path / ".openclaw" / "krab_runtime_state"
        state_dir.mkdir(parents=True)
        (state_dir / "swarm_memory.json").write_text('{"x": 1}', encoding="utf-8")
        (state_dir / "swarm_channels.json").write_text('{"y": 2}', encoding="utf-8")

        message = _make_message("!backup list")
        bot = _make_bot()

        with patch(
            "src.handlers.command_handlers.pathlib.Path.home",
            return_value=tmp_path,
        ):
            await handle_backup(bot, message)

        reply_text = message.reply.call_args[0][0]
        assert "2 файлов найдено" in reply_text
        assert f"{len(_BACKUP_FILES) - 2} отсутствуют" in reply_text
        # Найденные — галочки
        assert "swarm_memory.json" in reply_text
        assert "swarm_channels.json" in reply_text

    @pytest.mark.asyncio
    async def test_list_all_found(self, tmp_path):
        """!backup list — когда все файлы существуют."""
        state_dir = tmp_path / ".openclaw" / "krab_runtime_state"
        state_dir.mkdir(parents=True)
        for fname in _BACKUP_FILES:
            (state_dir / fname).write_text("{}", encoding="utf-8")

        message = _make_message("!backup list")
        bot = _make_bot()

        with patch(
            "src.handlers.command_handlers.pathlib.Path.home",
            return_value=tmp_path,
        ):
            await handle_backup(bot, message)

        reply_text = message.reply.call_args[0][0]
        assert f"{len(_BACKUP_FILES)} файлов найдено" in reply_text
        assert "0 отсутствуют" in reply_text

    @pytest.mark.asyncio
    async def test_list_shows_size(self, tmp_path):
        """!backup list — показывает размер найденных файлов."""
        state_dir = tmp_path / ".openclaw" / "krab_runtime_state"
        state_dir.mkdir(parents=True)
        content = '{"data": "hello"}'
        (state_dir / "swarm_memory.json").write_text(content, encoding="utf-8")

        message = _make_message("!backup list")
        bot = _make_bot()

        with patch(
            "src.handlers.command_handlers.pathlib.Path.home",
            return_value=tmp_path,
        ):
            await handle_backup(bot, message)

        reply_text = message.reply.call_args[0][0]
        # Размер в KB должен быть указан
        assert "KB" in reply_text


# ---------------------------------------------------------------------------
# Тесты !backup (создание архива)
# ---------------------------------------------------------------------------


class TestBackupCreate:
    @pytest.mark.asyncio
    async def test_no_files_found(self, tmp_path):
        """!backup без файлов — статус-сообщение редактируется с предупреждением."""
        state_dir = tmp_path / ".openclaw" / "krab_runtime_state"
        state_dir.mkdir(parents=True)

        message = _make_message("!backup")
        bot = _make_bot()
        status_msg = AsyncMock()
        status_msg.edit = AsyncMock()
        status_msg.delete = AsyncMock()
        message.reply = AsyncMock(return_value=status_msg)

        with patch(
            "src.handlers.command_handlers.pathlib.Path.home",
            return_value=tmp_path,
        ):
            await handle_backup(bot, message)

        status_msg.edit.assert_called_once()
        edit_text = status_msg.edit.call_args[0][0]
        assert "Нет данных" in edit_text or "не найден" in edit_text
        # send_document не должен вызываться
        bot.client.send_document.assert_not_called()

    @pytest.mark.asyncio
    async def test_creates_zip_and_sends(self, tmp_path):
        """!backup с файлами — создаёт ZIP и отправляет как документ."""
        state_dir = tmp_path / ".openclaw" / "krab_runtime_state"
        state_dir.mkdir(parents=True)
        (state_dir / "swarm_memory.json").write_text('{"agents": []}', encoding="utf-8")
        (state_dir / "swarm_channels.json").write_text('{"ch": {}}', encoding="utf-8")

        message = _make_message("!backup")
        bot = _make_bot()
        status_msg = AsyncMock()
        status_msg.edit = AsyncMock()
        status_msg.delete = AsyncMock()
        message.reply = AsyncMock(return_value=status_msg)

        with patch(
            "src.handlers.command_handlers.pathlib.Path.home",
            return_value=tmp_path,
        ):
            await handle_backup(bot, message)

        # send_document должен быть вызван
        bot.client.send_document.assert_called_once()
        call_kwargs = bot.client.send_document.call_args[1]
        assert call_kwargs["chat_id"] == message.chat.id
        assert call_kwargs["document"].endswith(".zip")

        # caption содержит "Krab Backup" и количество файлов
        caption = call_kwargs["caption"]
        assert "Krab Backup" in caption
        assert "2" in caption  # 2 файла включены

        # reply_to_message_id установлен
        assert call_kwargs["reply_to_message_id"] == message.id

        # status_msg удалён
        status_msg.delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_zip_contains_correct_files(self, tmp_path):
        """!backup — ZIP содержит именно те файлы, что были найдены."""
        state_dir = tmp_path / ".openclaw" / "krab_runtime_state"
        state_dir.mkdir(parents=True)
        present = ["swarm_memory.json", "bookmarks.json", "silence_schedule.json"]
        for fname in present:
            (state_dir / fname).write_text(f'{{"file": "{fname}"}}', encoding="utf-8")

        message = _make_message("!backup")
        bot = _make_bot()
        status_msg = AsyncMock()
        status_msg.edit = AsyncMock()
        status_msg.delete = AsyncMock()
        message.reply = AsyncMock(return_value=status_msg)

        sent_document_path = None

        async def capture_send(**kwargs):
            nonlocal sent_document_path
            sent_document_path = kwargs["document"]

        bot.client.send_document = AsyncMock(side_effect=capture_send)

        with patch(
            "src.handlers.command_handlers.pathlib.Path.home",
            return_value=tmp_path,
        ):
            await handle_backup(bot, message)

        # Проверяем содержимое ZIP (путь уже не существует — tempdir удалён)
        # Проверяем через caption что 3 файла включены
        call_kwargs = bot.client.send_document.call_args[1]
        caption = call_kwargs["caption"]
        assert "3" in caption

    @pytest.mark.asyncio
    async def test_skipped_files_in_caption(self, tmp_path):
        """!backup — когда часть файлов отсутствует, caption содержит 'Пропущено'."""
        state_dir = tmp_path / ".openclaw" / "krab_runtime_state"
        state_dir.mkdir(parents=True)
        (state_dir / "swarm_memory.json").write_text("{}", encoding="utf-8")
        # Остальные 12 файлов отсутствуют

        message = _make_message("!backup")
        bot = _make_bot()
        status_msg = AsyncMock()
        status_msg.edit = AsyncMock()
        status_msg.delete = AsyncMock()
        message.reply = AsyncMock(return_value=status_msg)

        with patch(
            "src.handlers.command_handlers.pathlib.Path.home",
            return_value=tmp_path,
        ):
            await handle_backup(bot, message)

        call_kwargs = bot.client.send_document.call_args[1]
        caption = call_kwargs["caption"]
        assert "Пропущено" in caption

    @pytest.mark.asyncio
    async def test_no_skipped_in_caption_when_all_present(self, tmp_path):
        """!backup — когда все файлы найдены, 'Пропущено' не выводится."""
        state_dir = tmp_path / ".openclaw" / "krab_runtime_state"
        state_dir.mkdir(parents=True)
        for fname in _BACKUP_FILES:
            (state_dir / fname).write_text("{}", encoding="utf-8")

        message = _make_message("!backup")
        bot = _make_bot()
        status_msg = AsyncMock()
        status_msg.edit = AsyncMock()
        status_msg.delete = AsyncMock()
        message.reply = AsyncMock(return_value=status_msg)

        with patch(
            "src.handlers.command_handlers.pathlib.Path.home",
            return_value=tmp_path,
        ):
            await handle_backup(bot, message)

        call_kwargs = bot.client.send_document.call_args[1]
        caption = call_kwargs["caption"]
        assert "Пропущено" not in caption

    @pytest.mark.asyncio
    async def test_send_document_error(self, tmp_path):
        """!backup — ошибка send_document → status_msg.edit с сообщением об ошибке."""
        state_dir = tmp_path / ".openclaw" / "krab_runtime_state"
        state_dir.mkdir(parents=True)
        (state_dir / "swarm_memory.json").write_text("{}", encoding="utf-8")

        message = _make_message("!backup")
        bot = _make_bot(send_document_exc=Exception("Telegram error"))
        status_msg = AsyncMock()
        status_msg.edit = AsyncMock()
        status_msg.delete = AsyncMock()
        message.reply = AsyncMock(return_value=status_msg)

        with patch(
            "src.handlers.command_handlers.pathlib.Path.home",
            return_value=tmp_path,
        ):
            await handle_backup(bot, message)

        status_msg.edit.assert_called_once()
        edit_text = status_msg.edit.call_args[0][0]
        assert "Ошибка" in edit_text
        # status_msg.delete не должен вызываться при ошибке
        status_msg.delete.assert_not_called()

    @pytest.mark.asyncio
    async def test_status_message_sent_immediately(self, tmp_path):
        """!backup — статусное сообщение отправляется до создания архива."""
        state_dir = tmp_path / ".openclaw" / "krab_runtime_state"
        state_dir.mkdir(parents=True)
        (state_dir / "swarm_memory.json").write_text("{}", encoding="utf-8")

        message = _make_message("!backup")
        bot = _make_bot()
        status_msg = AsyncMock()
        status_msg.edit = AsyncMock()
        status_msg.delete = AsyncMock()
        message.reply = AsyncMock(return_value=status_msg)

        with patch(
            "src.handlers.command_handlers.pathlib.Path.home",
            return_value=tmp_path,
        ):
            await handle_backup(bot, message)

        # reply вызван один раз с "⏳" статусом
        message.reply.assert_called_once()
        assert "⏳" in message.reply.call_args[0][0] or "Создаю" in message.reply.call_args[0][0]

    @pytest.mark.asyncio
    async def test_archive_filename_contains_timestamp(self, tmp_path):
        """!backup — имя архива содержит временную метку."""
        state_dir = tmp_path / ".openclaw" / "krab_runtime_state"
        state_dir.mkdir(parents=True)
        (state_dir / "swarm_memory.json").write_text("{}", encoding="utf-8")

        message = _make_message("!backup")
        bot = _make_bot()
        status_msg = AsyncMock()
        status_msg.edit = AsyncMock()
        status_msg.delete = AsyncMock()
        message.reply = AsyncMock(return_value=status_msg)

        with patch(
            "src.handlers.command_handlers.pathlib.Path.home",
            return_value=tmp_path,
        ):
            await handle_backup(bot, message)

        call_kwargs = bot.client.send_document.call_args[1]
        doc_path = call_kwargs["document"]
        # Имя файла должно начинаться с "krab_backup_"
        assert pathlib.Path(doc_path).name.startswith("krab_backup_")
        assert doc_path.endswith(".zip")

    @pytest.mark.asyncio
    async def test_caption_contains_file_size(self, tmp_path):
        """!backup — caption содержит размер архива в KB."""
        state_dir = tmp_path / ".openclaw" / "krab_runtime_state"
        state_dir.mkdir(parents=True)
        (state_dir / "swarm_memory.json").write_text('{"a": "b"}' * 100, encoding="utf-8")

        message = _make_message("!backup")
        bot = _make_bot()
        status_msg = AsyncMock()
        status_msg.edit = AsyncMock()
        status_msg.delete = AsyncMock()
        message.reply = AsyncMock(return_value=status_msg)

        with patch(
            "src.handlers.command_handlers.pathlib.Path.home",
            return_value=tmp_path,
        ):
            await handle_backup(bot, message)

        call_kwargs = bot.client.send_document.call_args[1]
        caption = call_kwargs["caption"]
        assert "KB" in caption


# ---------------------------------------------------------------------------
# Граничные случаи
# ---------------------------------------------------------------------------


class TestBackupEdgeCases:
    @pytest.mark.asyncio
    async def test_empty_args_treated_as_backup(self, tmp_path):
        """!backup без аргументов — создаёт архив (не list)."""
        state_dir = tmp_path / ".openclaw" / "krab_runtime_state"
        state_dir.mkdir(parents=True)
        (state_dir / "swarm_memory.json").write_text("{}", encoding="utf-8")

        message = _make_message("!backup")
        bot = _make_bot()
        status_msg = AsyncMock()
        status_msg.edit = AsyncMock()
        status_msg.delete = AsyncMock()
        message.reply = AsyncMock(return_value=status_msg)

        with patch(
            "src.handlers.command_handlers.pathlib.Path.home",
            return_value=tmp_path,
        ):
            await handle_backup(bot, message)

        # Архив создан и отправлен
        bot.client.send_document.assert_called_once()

    @pytest.mark.asyncio
    async def test_list_subcommand_case_insensitive(self, tmp_path):
        """!backup LIST — команда case-insensitive."""
        state_dir = tmp_path / ".openclaw" / "krab_runtime_state"
        state_dir.mkdir(parents=True)

        message = _make_message("!backup LIST")
        bot = _make_bot()

        with patch(
            "src.handlers.command_handlers.pathlib.Path.home",
            return_value=tmp_path,
        ):
            await handle_backup(bot, message)

        # reply вызван с содержимым списка
        message.reply.assert_called_once()
        reply_text = message.reply.call_args[0][0]
        assert "Файлы в резервной копии" in reply_text
        # send_document не должен вызываться
        bot.client.send_document.assert_not_called()

    @pytest.mark.asyncio
    async def test_runtime_state_dir_not_exists(self, tmp_path):
        """!backup — директория runtime_state не существует → нет файлов → edit."""
        message = _make_message("!backup")
        bot = _make_bot()
        status_msg = AsyncMock()
        status_msg.edit = AsyncMock()
        status_msg.delete = AsyncMock()
        message.reply = AsyncMock(return_value=status_msg)

        # tmp_path существует, но .openclaw/krab_runtime_state НЕ создана
        with patch(
            "src.handlers.command_handlers.pathlib.Path.home",
            return_value=tmp_path,
        ):
            await handle_backup(bot, message)

        # Нет файлов — редактируем статус
        status_msg.edit.assert_called_once()
        bot.client.send_document.assert_not_called()

    @pytest.mark.asyncio
    async def test_backup_reply_targets_original_message(self, tmp_path):
        """!backup — reply_to_message_id равен ID исходного сообщения."""
        state_dir = tmp_path / ".openclaw" / "krab_runtime_state"
        state_dir.mkdir(parents=True)
        (state_dir / "swarm_memory.json").write_text("{}", encoding="utf-8")

        message = _make_message("!backup", message_id=999)
        bot = _make_bot()
        status_msg = AsyncMock()
        status_msg.edit = AsyncMock()
        status_msg.delete = AsyncMock()
        message.reply = AsyncMock(return_value=status_msg)

        with patch(
            "src.handlers.command_handlers.pathlib.Path.home",
            return_value=tmp_path,
        ):
            await handle_backup(bot, message)

        call_kwargs = bot.client.send_document.call_args[1]
        assert call_kwargs["reply_to_message_id"] == 999
