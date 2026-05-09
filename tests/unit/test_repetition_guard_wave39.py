# -*- coding: utf-8 -*-
"""Wave 39-A tests: RepetitionGuard — защита от повторных похожих ответов.

TDD RED → GREEN. Проверяем:
1. Пустой store → не репетиция
2. Точный дубликат в окне → True
3. Перефраз (~70% overlap) → True при дефолтном threshold 0.6
4. Несвязный текст → False
5. Старая запись за пределами window_sec → False
6. FIFO eviction: после max_size+1 записей самая старая вытеснена
7. Разные chat_id изолированы
8. Стоп-слова игнорируются при расчёте similarity
"""

from __future__ import annotations

import time

import pytest

from src.core.repetition_guard import RepetitionGuard, _jaccard, _tokenize

# ---------------------------------------------------------------------------
# Вспомогательные фикстуры
# ---------------------------------------------------------------------------


@pytest.fixture()
def guard() -> RepetitionGuard:
    """Свежий guard с дефолтными настройками."""
    return RepetitionGuard(max_size=5)


# ---------------------------------------------------------------------------
# _tokenize / _jaccard unit tests
# ---------------------------------------------------------------------------


def test_tokenize_lowercase_and_split() -> None:
    tokens = _tokenize("Привет Мир")
    assert "привет" in tokens
    assert "мир" in tokens


def test_tokenize_removes_stop_words() -> None:
    tokens = _tokenize("и в на с 🦀 — hello")
    # Стоп-слова убраны
    assert "и" not in tokens
    assert "в" not in tokens
    assert "на" not in tokens
    assert "🦀" not in tokens
    assert "—" not in tokens
    # "hello" — не стоп-слово
    assert "hello" in tokens


def test_tokenize_empty() -> None:
    assert _tokenize("") == frozenset()


def test_tokenize_only_stop_words() -> None:
    tokens = _tokenize("и в на с")
    assert tokens == frozenset()


def test_jaccard_identical() -> None:
    a = frozenset({"кот", "сидит", "дома"})
    assert _jaccard(a, a) == pytest.approx(1.0)


def test_jaccard_disjoint() -> None:
    a = frozenset({"кот"})
    b = frozenset({"собака"})
    assert _jaccard(a, b) == pytest.approx(0.0)


def test_jaccard_partial_overlap() -> None:
    a = frozenset({"a", "b", "c"})
    b = frozenset({"b", "c", "d"})
    # intersection=2, union=4 → 0.5
    assert _jaccard(a, b) == pytest.approx(0.5)


def test_jaccard_both_empty() -> None:
    # Оба пустые → 1.0 (идентичны)
    assert _jaccard(frozenset(), frozenset()) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# RepetitionGuard tests
# ---------------------------------------------------------------------------


class TestRepetitionGuardEmptyStore:
    def test_no_history_is_not_repetition(self, guard: RepetitionGuard) -> None:
        """1. Пустой store → not repetition."""
        assert guard.is_repetition(42, "любой текст") is False

    def test_different_chat_empty_is_not_repetition(self, guard: RepetitionGuard) -> None:
        guard.record(1, "что-то написали в чате 1")
        # Чат 2 — пустой
        assert guard.is_repetition(2, "что-то написали в чате 1") is False


class TestRepetitionGuardExactDuplicate:
    def test_exact_duplicate_within_window(self, guard: RepetitionGuard) -> None:
        """2. Точный дубликат в окне → True."""
        text = "Всё хорошо, не беспокойся, всё под контролем"
        guard.record(100, text)
        assert guard.is_repetition(100, text) is True

    def test_exact_duplicate_different_case(self, guard: RepetitionGuard) -> None:
        """Регистр не важен — токенизация lowercase."""
        guard.record(100, "КРАБ УМНЫЙ")
        assert guard.is_repetition(100, "краб умный") is True


class TestRepetitionGuardParaphrase:
    def test_paraphrase_high_overlap(self, guard: RepetitionGuard) -> None:
        """3. Перефраз с ~70% токенов → True при threshold=0.6."""
        original = "рынок растёт быстро доллар укрепляется евро падает"
        paraphrase = "доллар укрепляется рынок растёт быстро евро слабеет"
        guard.record(200, original)
        # Перекрытие достаточно высокое
        result = guard.is_repetition(200, paraphrase, threshold=0.6)
        assert result is True

    def test_reversed_word_order(self, guard: RepetitionGuard) -> None:
        """Jaccard не зависит от порядка слов."""
        text = "кот сидит дома ждёт хозяина"
        reversed_text = "хозяина ждёт дома сидит кот"
        guard.record(300, text)
        assert guard.is_repetition(300, reversed_text) is True


class TestRepetitionGuardUnrelated:
    def test_unrelated_text_not_repetition(self, guard: RepetitionGuard) -> None:
        """4. Несвязный текст → False."""
        guard.record(400, "рынок растёт доллар укрепляется евро падает")
        assert guard.is_repetition(400, "сегодня хорошая погода прогулка парк") is False

    def test_low_overlap_not_repetition(self, guard: RepetitionGuard) -> None:
        guard.record(400, "альфа бета гамма дельта эпсилон зета")
        # только одно общее слово из большого множества
        assert guard.is_repetition(400, "альфа один два три четыре пять шесть семь") is False


class TestRepetitionGuardExpiry:
    def test_old_entry_beyond_window_not_repetition(self, guard: RepetitionGuard) -> None:
        """5. Запись за пределами window_sec → False."""
        now = time.monotonic()
        # Записываем с ts в прошлом (700 секунд назад)
        old_ts = now - 700
        text = "повторяющийся ответ краба пользователю"
        guard.record(500, text, ts=old_ts)
        # Окно = 600 секунд — запись протухла
        assert guard.is_repetition(500, text, window_sec=600) is False

    def test_recent_entry_within_window_is_repetition(self, guard: RepetitionGuard) -> None:
        now = time.monotonic()
        recent_ts = now - 100  # 100 секунд назад — в пределах окна 600с
        text = "свежий ответ краба"
        guard.record(500, text, ts=recent_ts)
        assert guard.is_repetition(500, text, window_sec=600) is True


class TestRepetitionGuardFIFO:
    def test_max_size_evicts_oldest(self) -> None:
        """6. После max_size+1 записей самая старая вытеснена."""
        guard = RepetitionGuard(max_size=5)
        oldest_text = "самый старый ответ который будет вытеснен скоро"
        guard.record(600, oldest_text)  # oldest
        for i in range(5):
            guard.record(600, f"новый ответ номер {i} заполняет deque")
        # Oldest должен быть вытеснен (deque maxlen=5)
        # Проверяем через внутренний store: oldest не в deque
        stored = list(guard._store.get("600", []))
        oldest_tokens = _tokenize(oldest_text)
        assert oldest_tokens not in [tok for tok, _ in stored]

    def test_max_size_keeps_recent(self) -> None:
        """Последний записанный элемент остаётся."""
        guard = RepetitionGuard(max_size=3)
        for i in range(3):
            guard.record(601, f"запись {i} разные слова не похожи")
        last_text = "финальный уникальный ответ краба в чате"
        guard.record(601, last_text)
        # last_text должен быть в store (вытеснил самую раннюю)
        assert guard.is_repetition(601, last_text) is True


class TestRepetitionGuardIsolation:
    def test_different_chat_ids_isolated(self, guard: RepetitionGuard) -> None:
        """7. Разные chat_id изолированы."""
        text = "одинаковый текст для обоих чатов"
        guard.record(700, text)
        # Чат 701 — история пустая → не репетиция
        assert guard.is_repetition(701, text) is False

    def test_int_and_str_chat_id_same_namespace(self, guard: RepetitionGuard) -> None:
        """int и str chat_id конвертируются к str — один namespace."""
        text = "тест namespace int vs str"
        guard.record(800, text)  # int
        assert guard.is_repetition("800", text) is True  # str → та же история


class TestRepetitionGuardStopWords:
    def test_stop_word_only_texts_both_empty(self, guard: RepetitionGuard) -> None:
        """8. Тексты только из стоп-слов → оба tokenize в frozenset() → Jaccard=1.0."""
        guard.record(900, "и в на с")  # все стоп-слова → пустой набор
        # candidate тоже только стоп-слова → пустой набор → Jaccard 1.0 → True
        assert guard.is_repetition(900, "но да то же") is True

    def test_stop_words_ignored_in_similarity(self, guard: RepetitionGuard) -> None:
        """Добавление стоп-слов не меняет similarity."""
        text_a = "краб умный быстрый надёжный"
        text_b = "и краб и умный и быстрый и надёжный"  # те же слова + стоп-союзы
        guard.record(901, text_a)
        assert guard.is_repetition(901, text_b) is True


class TestRepetitionGuardThreshold:
    def test_high_threshold_not_repetition(self, guard: RepetitionGuard) -> None:
        """При высоком threshold=0.95 перефраз не детектируется."""
        guard.record(1000, "рынок растёт доллар укрепляется евро падает")
        # threshold 0.95 — только почти точные дубли пройдут
        assert (
            guard.is_repetition(
                1000, "рынок растёт доллар укрепляется нефть дорожает", threshold=0.95
            )
            is False
        )

    def test_low_threshold_catches_loose_overlap(self, guard: RepetitionGuard) -> None:
        guard.record(1001, "рынок растёт доллар укрепляется евро")
        assert guard.is_repetition(1001, "рынок укрепляется", threshold=0.2) is True
