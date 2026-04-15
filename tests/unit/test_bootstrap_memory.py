"""
Unit-тесты ``scripts/bootstrap_memory.py``.

Покрывают:
  * ``extract_text()`` — три формата (string / array / empty);
  * ``detect_export_format()`` — single vs multi vs unknown;
  * ``run_bootstrap(dry_run=True)`` — не пишет в БД, возвращает stats;
  * Полный прогон на fixture + in-memory БД:
    - правильное количество chats/messages/chunks;
    - FTS search находит текст;
    - PII отредактирован (поиск "4242 4242" даёт 0 строк);
    - service messages пропущены;
    - media-only сообщения без текста пропущены.

Фикстура: ``tests/fixtures/telegram_export_sample.json`` — 36 сообщений,
single-chat (private_supergroup), id=-1003703978531.

Запуск (ожидаемо без conftest, чтобы обойти зависимости userbot_bridge)::

    venv/bin/python -m pytest tests/unit/test_bootstrap_memory.py -q --noconftest
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

# Прогружаем project root, чтобы src.* и scripts.* импортировались.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from scripts.bootstrap_memory import (  # noqa: E402
    _CHAT_TYPE_MAP,
    BootstrapStats,
    _chunk_hash,
    detect_export_format,
    extract_text,
    iter_chats,
    run_bootstrap,
)

FIXTURE_PATH = _PROJECT_ROOT / "tests" / "fixtures" / "telegram_export_sample.json"
FIXTURE_CHAT_ID = "-1003703978531"


# ---------------------------------------------------------------------------
# extract_text — три формата.
# ---------------------------------------------------------------------------

class TestExtractText:
    def test_plain_string(self) -> None:
        msg = {"text": "привет мир"}
        assert extract_text(msg) == "привет мир"

    def test_array_with_entities(self) -> None:
        # Формат из fixture id=13: строки + форматированные сегменты.
        msg = {
            "text": [
                "смотрите функцию ",
                {"type": "code", "text": "split_into_chunks()"},
                " в ",
                {"type": "bold", "text": "memory/chunker.py"},
            ]
        }
        result = extract_text(msg)
        assert "смотрите функцию " in result
        assert "split_into_chunks()" in result
        assert "memory/chunker.py" in result
        # Порядок сохранён.
        assert result.index("смотрите") < result.index("split_into_chunks()")

    def test_empty_text_and_no_entities(self) -> None:
        # Media-message: text="", entities=[].
        msg = {"text": "", "text_entities": []}
        assert extract_text(msg) == ""

    def test_missing_text_fallback_to_entities(self) -> None:
        msg = {
            "text_entities": [
                {"type": "plain", "text": "fallback"},
                {"type": "code", "text": " value"},
            ]
        }
        assert extract_text(msg) == "fallback value"

    def test_none_text(self) -> None:
        assert extract_text({"text": None}) == ""

    def test_array_with_none_segments(self) -> None:
        # Защита от мусора внутри массива.
        msg = {"text": ["ok", None, {"type": "plain", "text": "!"}]}
        assert extract_text(msg) == "ok!"


# ---------------------------------------------------------------------------
# detect_export_format / iter_chats.
# ---------------------------------------------------------------------------

class TestFormatDetection:
    def test_single_chat_format(self) -> None:
        data = {"name": "X", "type": "personal_chat", "id": 1, "messages": []}
        assert detect_export_format(data) == "single"

    def test_multi_chat_format(self) -> None:
        data = {"chats": {"list": [{"id": 1, "messages": []}]}}
        assert detect_export_format(data) == "multi"

    def test_unknown_format(self) -> None:
        assert detect_export_format({}) == "unknown"
        assert detect_export_format({"about": "no messages anywhere"}) == "unknown"

    def test_iter_chats_single(self) -> None:
        data = {"name": "Y", "id": 2, "messages": []}
        chats = list(iter_chats(data))
        assert len(chats) == 1
        assert chats[0]["id"] == 2

    def test_iter_chats_multi(self) -> None:
        data = {
            "chats": {
                "list": [
                    {"id": 1, "messages": []},
                    {"id": 2, "messages": []},
                ]
            }
        }
        chats = list(iter_chats(data))
        assert [c["id"] for c in chats] == [1, 2]


# ---------------------------------------------------------------------------
# Вспомогательные.
# ---------------------------------------------------------------------------

class TestChunkHash:
    def test_deterministic(self) -> None:
        assert _chunk_hash("c1", "10") == _chunk_hash("c1", "10")

    def test_different_inputs_differ(self) -> None:
        a = _chunk_hash("c1", "10")
        b = _chunk_hash("c1", "11")
        c = _chunk_hash("c2", "10")
        assert len({a, b, c}) == 3

    def test_hex_length(self) -> None:
        assert len(_chunk_hash("x", "y")) == 16


class TestChatTypeMap:
    def test_known_types_mapped(self) -> None:
        assert _CHAT_TYPE_MAP["private_supergroup"] == "supergroup"
        assert _CHAT_TYPE_MAP["bot_chat"] == "private"
        assert _CHAT_TYPE_MAP["private_channel"] == "channel"


# ---------------------------------------------------------------------------
# Whitelist fixture — allow fixture chat.
# ---------------------------------------------------------------------------

@pytest.fixture
def allowed_whitelist(tmp_path: Path) -> Path:
    """Whitelist.json, разрешающий chat id фикстуры."""
    cfg = {
        "allow_all": False,
        "allow": {"ids": [FIXTURE_CHAT_ID], "title_regex": []},
        "deny": {"ids": [], "title_regex": []},
    }
    path = tmp_path / "whitelist.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    return path


@pytest.fixture
def deny_whitelist(tmp_path: Path) -> Path:
    """Whitelist.json с deny — чат фикстуры запрещён."""
    cfg = {
        "allow_all": False,
        "allow": {"ids": [], "title_regex": []},
        "deny": {"ids": [FIXTURE_CHAT_ID], "title_regex": []},
    }
    path = tmp_path / "whitelist.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Dry-run.
# ---------------------------------------------------------------------------

class TestDryRun:
    def test_dry_run_does_not_touch_db(
        self, tmp_path: Path, allowed_whitelist: Path
    ) -> None:
        # Dry-run: даже если db_path передан, файл не создаётся.
        fake_db = tmp_path / "must_not_exist.db"
        stats = run_bootstrap(
            export_path=FIXTURE_PATH,
            db_path=fake_db,
            whitelist_path=allowed_whitelist,
            dry_run=True,
        )
        assert not fake_db.exists()
        # Stats наполнена — значит парсинг прошёл.
        assert stats.messages_read > 0
        assert stats.messages_processed > 0
        assert stats.chunks_created > 0
        assert stats.chats_indexed == 1

    def test_dry_run_preview_chunks(self, allowed_whitelist: Path) -> None:
        stats = run_bootstrap(
            export_path=FIXTURE_PATH,
            whitelist_path=allowed_whitelist,
            dry_run=True,
        )
        assert stats.preview_chunks  # что-то отдалось
        assert len(stats.preview_chunks) <= 10
        sample = stats.preview_chunks[0]
        assert "chunk_id" in sample
        assert sample["messages"] >= 1

    def test_service_messages_skipped(self, allowed_whitelist: Path) -> None:
        """В фикстуре id=16 и id=36 — service messages; не попадают в processed."""
        stats = run_bootstrap(
            export_path=FIXTURE_PATH,
            whitelist_path=allowed_whitelist,
            dry_run=True,
        )
        assert stats.messages_skipped.get("service_message", 0) >= 2

    def test_media_only_skipped(self, allowed_whitelist: Path) -> None:
        """id=27 (photo), 29 (voice), 30 (video) — text пустой, skip reason=empty_text."""
        stats = run_bootstrap(
            export_path=FIXTURE_PATH,
            whitelist_path=allowed_whitelist,
            dry_run=True,
        )
        assert stats.messages_skipped.get("empty_text", 0) >= 3

    def test_whitelist_deny_skips_chat(self, deny_whitelist: Path) -> None:
        stats = run_bootstrap(
            export_path=FIXTURE_PATH,
            whitelist_path=deny_whitelist,
            dry_run=True,
        )
        assert stats.chats_indexed == 0
        assert stats.chats_skipped == 1
        assert stats.messages_processed == 0

    def test_allow_all_overrides_empty_whitelist(self, tmp_path: Path) -> None:
        empty = tmp_path / "wl.json"
        empty.write_text(json.dumps({}), encoding="utf-8")
        stats = run_bootstrap(
            export_path=FIXTURE_PATH,
            whitelist_path=empty,
            allow_all=True,
            dry_run=True,
        )
        assert stats.chats_indexed == 1


# ---------------------------------------------------------------------------
# Полный прогон на :memory:.
# ---------------------------------------------------------------------------

@pytest.fixture
def memory_conn() -> sqlite3.Connection:
    """In-memory SQLite — скрипт поднимет схему сам."""
    conn = sqlite3.connect(":memory:")
    try:
        yield conn
    finally:
        conn.close()


class TestFullRun:
    def test_full_pipeline_populates_tables(
        self,
        memory_conn: sqlite3.Connection,
        allowed_whitelist: Path,
    ) -> None:
        stats = run_bootstrap(
            export_path=FIXTURE_PATH,
            whitelist_path=allowed_whitelist,
            dry_run=False,
            in_memory_conn=memory_conn,
        )

        # Chat row.
        chat_rows = memory_conn.execute(
            "SELECT chat_id, title, chat_type, message_count FROM chats;"
        ).fetchall()
        assert len(chat_rows) == 1
        cid, title, ctype, mcount = chat_rows[0]
        assert cid == FIXTURE_CHAT_ID
        assert ctype == "supergroup"
        # После update_chat_counters message_count совпадает с processed.
        assert mcount == stats.messages_processed

        # Messages.
        msg_count = memory_conn.execute(
            "SELECT COUNT(*) FROM messages;"
        ).fetchone()[0]
        assert msg_count == stats.messages_processed

        # Chunks.
        chunk_count = memory_conn.execute(
            "SELECT COUNT(*) FROM chunks;"
        ).fetchone()[0]
        assert chunk_count == stats.chunks_created
        assert chunk_count >= 1

        # chunk_messages M2M >= chunks (каждый chunk хотя бы одно сообщение).
        m2m_count = memory_conn.execute(
            "SELECT COUNT(*) FROM chunk_messages;"
        ).fetchone()[0]
        assert m2m_count >= chunk_count

    def test_fts_search_returns_matches(
        self,
        memory_conn: sqlite3.Connection,
        allowed_whitelist: Path,
    ) -> None:
        run_bootstrap(
            export_path=FIXTURE_PATH,
            whitelist_path=allowed_whitelist,
            in_memory_conn=memory_conn,
        )
        # В fixture есть слово "chunker" (id=12, id=13).
        rows = memory_conn.execute(
            "SELECT text_redacted FROM messages_fts WHERE messages_fts MATCH 'chunker';"
        ).fetchall()
        assert rows, "FTS должен находить 'chunker'"
        assert any("chunker" in r[0] for r in rows)

    def test_pii_redacted_card_not_in_db(
        self,
        memory_conn: sqlite3.Connection,
        allowed_whitelist: Path,
    ) -> None:
        run_bootstrap(
            export_path=FIXTURE_PATH,
            whitelist_path=allowed_whitelist,
            in_memory_conn=memory_conn,
        )
        # Сообщение id=23 содержит "4242 4242 4242 4242" — должно быть redacted.
        rows = memory_conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE text_redacted LIKE '%4242 4242%';"
        ).fetchone()[0]
        assert rows == 0, "PII redactor должен был стереть номер карты"

        msg_rows = memory_conn.execute(
            "SELECT COUNT(*) FROM messages WHERE text_redacted LIKE '%4242 4242%';"
        ).fetchone()[0]
        assert msg_rows == 0

        # Зато placeholder присутствует.
        placeholder = memory_conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE text_redacted LIKE '%[REDACTED:CARD]%';"
        ).fetchone()[0]
        assert placeholder >= 1

    def test_email_redacted(
        self,
        memory_conn: sqlite3.Connection,
        allowed_whitelist: Path,
    ) -> None:
        run_bootstrap(
            export_path=FIXTURE_PATH,
            whitelist_path=allowed_whitelist,
            in_memory_conn=memory_conn,
        )
        # Сообщение id=25 содержит someone@example.com.
        rows = memory_conn.execute(
            "SELECT COUNT(*) FROM messages WHERE text_redacted LIKE '%someone@example.com%';"
        ).fetchone()[0]
        assert rows == 0

    def test_idempotent_rerun(
        self,
        memory_conn: sqlite3.Connection,
        allowed_whitelist: Path,
    ) -> None:
        stats_1 = run_bootstrap(
            export_path=FIXTURE_PATH,
            whitelist_path=allowed_whitelist,
            in_memory_conn=memory_conn,
        )
        # После первого прогона перезапускаем на том же conn.
        before_chunks = memory_conn.execute(
            "SELECT COUNT(*) FROM chunks;"
        ).fetchone()[0]
        stats_2 = run_bootstrap(
            export_path=FIXTURE_PATH,
            whitelist_path=allowed_whitelist,
            in_memory_conn=memory_conn,
        )
        after_chunks = memory_conn.execute(
            "SELECT COUNT(*) FROM chunks;"
        ).fetchone()[0]
        # Idempotent: количество chunks одно и то же.
        assert before_chunks == after_chunks
        assert stats_1.chunks_created == stats_2.chunks_created

    def test_limit_option_caps_messages(
        self,
        memory_conn: sqlite3.Connection,
        allowed_whitelist: Path,
    ) -> None:
        stats = run_bootstrap(
            export_path=FIXTURE_PATH,
            whitelist_path=allowed_whitelist,
            in_memory_conn=memory_conn,
            limit=5,
        )
        assert stats.messages_processed <= 5


# ---------------------------------------------------------------------------
# Validation / errors.
# ---------------------------------------------------------------------------

class TestErrors:
    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            run_bootstrap(export_path=tmp_path / "nope.json", dry_run=True)

    def test_unknown_format_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps({"about": "no chats here"}), encoding="utf-8")
        with pytest.raises(ValueError):
            run_bootstrap(export_path=bad, dry_run=True)

    def test_malformed_json_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        with pytest.raises(json.JSONDecodeError):
            run_bootstrap(export_path=bad, dry_run=True)


# ---------------------------------------------------------------------------
# BootstrapStats sanity.
# ---------------------------------------------------------------------------

class TestStatsSerialization:
    def test_as_dict_contains_core_keys(self) -> None:
        stats = BootstrapStats()
        stats.messages_read = 100
        stats.chunks_created = 12
        d = stats.as_dict()
        assert d["messages_read"] == 100
        assert d["chunks_created"] == 12
        assert "pii_total" in d

    def test_bump_skipped(self) -> None:
        stats = BootstrapStats()
        stats.bump_skipped("service_message")
        stats.bump_skipped("service_message")
        stats.bump_skipped("empty_text")
        assert stats.messages_skipped == {"service_message": 2, "empty_text": 1}
