# -*- coding: utf-8 -*-
"""
Тесты для src/core/telegram_buttons.py.

Проверяем структуру InlineKeyboardMarkup и callback_data для всех
вспомогательных функций построения кнопок.
"""

from __future__ import annotations

from pyrogram.types import InlineKeyboardMarkup

from src.core.telegram_buttons import (
    build_action_buttons,
    build_confirm_buttons,
    build_costs_detail_buttons,
    build_health_recheck_buttons,
    build_pagination_buttons,
    build_swarm_team_buttons,
)

# ─────────────────────────────────────────────────────────────────────────────
# build_confirm_buttons
# ─────────────────────────────────────────────────────────────────────────────


class TestBuildConfirmButtons:
    def test_returns_inline_keyboard_markup(self):
        markup = build_confirm_buttons("delete_item")
        assert isinstance(markup, InlineKeyboardMarkup)

    def test_single_row_two_buttons(self):
        markup = build_confirm_buttons("test_action")
        assert len(markup.inline_keyboard) == 1
        row = markup.inline_keyboard[0]
        assert len(row) == 2

    def test_yes_button_callback_data(self):
        markup = build_confirm_buttons("do_reset")
        yes_btn = markup.inline_keyboard[0][0]
        assert yes_btn.callback_data == "confirm:do_reset:yes"

    def test_no_button_callback_data(self):
        markup = build_confirm_buttons("do_reset")
        no_btn = markup.inline_keyboard[0][1]
        assert no_btn.callback_data == "confirm:do_reset:no"

    def test_yes_button_text(self):
        markup = build_confirm_buttons("x")
        assert "Да" in markup.inline_keyboard[0][0].text

    def test_no_button_text(self):
        markup = build_confirm_buttons("x")
        assert "Нет" in markup.inline_keyboard[0][1].text

    def test_action_id_preserved_in_callback(self):
        action_id = "archive:123"
        markup = build_confirm_buttons(action_id)
        yes_cb = markup.inline_keyboard[0][0].callback_data
        assert action_id in yes_cb

    def test_prefix_confirm_in_yes(self):
        markup = build_confirm_buttons("action")
        assert markup.inline_keyboard[0][0].callback_data.startswith("confirm:")

    def test_prefix_confirm_in_no(self):
        markup = build_confirm_buttons("action")
        assert markup.inline_keyboard[0][1].callback_data.startswith("confirm:")


# ─────────────────────────────────────────────────────────────────────────────
# build_pagination_buttons
# ─────────────────────────────────────────────────────────────────────────────


class TestBuildPaginationButtons:
    def test_returns_inline_keyboard_markup(self):
        markup = build_pagination_buttons(0, 3, "results")
        assert isinstance(markup, InlineKeyboardMarkup)

    def test_first_page_no_prev_button(self):
        # Страница 0 — нет кнопки «◀️»
        markup = build_pagination_buttons(0, 3, "res")
        buttons = markup.inline_keyboard[0]
        labels = [b.text for b in buttons]
        assert "◀️" not in labels

    def test_last_page_no_next_button(self):
        markup = build_pagination_buttons(2, 3, "res")
        buttons = markup.inline_keyboard[0]
        labels = [b.text for b in buttons]
        assert "▶️" not in labels

    def test_middle_page_has_both_arrows(self):
        markup = build_pagination_buttons(1, 3, "res")
        buttons = markup.inline_keyboard[0]
        labels = [b.text for b in buttons]
        assert "◀️" in labels
        assert "▶️" in labels

    def test_indicator_shows_current_page(self):
        markup = build_pagination_buttons(1, 5, "res")
        buttons = markup.inline_keyboard[0]
        indicator = next(b for b in buttons if "/" in b.text)
        assert "2/5" == indicator.text

    def test_next_page_callback_data(self):
        markup = build_pagination_buttons(0, 3, "myprefix")
        next_btn = markup.inline_keyboard[0][-1]
        assert next_btn.callback_data == "page:myprefix:1"

    def test_prev_page_callback_data(self):
        markup = build_pagination_buttons(2, 3, "myprefix")
        prev_btn = markup.inline_keyboard[0][0]
        assert prev_btn.callback_data == "page:myprefix:1"

    def test_noop_callback_for_indicator(self):
        markup = build_pagination_buttons(0, 3, "prefix")
        buttons = markup.inline_keyboard[0]
        indicator = next(b for b in buttons if "/" in b.text)
        assert indicator.callback_data == "page:prefix:noop"

    def test_single_page_only_indicator(self):
        markup = build_pagination_buttons(0, 1, "x")
        buttons = markup.inline_keyboard[0]
        assert len(buttons) == 1
        assert "1/1" == buttons[0].text

    def test_prefix_included_in_callback(self):
        markup = build_pagination_buttons(0, 2, "mypage")
        next_btn = markup.inline_keyboard[0][-1]
        assert "mypage" in next_btn.callback_data


# ─────────────────────────────────────────────────────────────────────────────
# build_action_buttons
# ─────────────────────────────────────────────────────────────────────────────


class TestBuildActionButtons:
    def test_returns_inline_keyboard_markup(self):
        markup = build_action_buttons([("Кнопка", "btn_id")])
        assert isinstance(markup, InlineKeyboardMarkup)

    def test_single_button(self):
        markup = build_action_buttons([("Go", "go")])
        assert len(markup.inline_keyboard) == 1
        assert len(markup.inline_keyboard[0]) == 1

    def test_callback_data_prefix(self):
        markup = build_action_buttons([("Go", "go")])
        btn = markup.inline_keyboard[0][0]
        assert btn.callback_data == "action:go"

    def test_columns_split(self):
        actions = [("A", "a"), ("B", "b"), ("C", "c"), ("D", "d")]
        markup = build_action_buttons(actions, columns=2)
        # 4 кнопки по 2 в строке → 2 строки
        assert len(markup.inline_keyboard) == 2
        assert len(markup.inline_keyboard[0]) == 2
        assert len(markup.inline_keyboard[1]) == 2

    def test_odd_count_last_row_partial(self):
        actions = [("A", "a"), ("B", "b"), ("C", "c")]
        markup = build_action_buttons(actions, columns=2)
        assert len(markup.inline_keyboard) == 2
        assert len(markup.inline_keyboard[1]) == 1

    def test_empty_actions_returns_empty_markup(self):
        markup = build_action_buttons([])
        assert markup.inline_keyboard == []

    def test_button_text_preserved(self):
        markup = build_action_buttons([("Старт", "start")])
        assert markup.inline_keyboard[0][0].text == "Старт"

    def test_multiple_buttons_one_column(self):
        actions = [("A", "a"), ("B", "b")]
        markup = build_action_buttons(actions, columns=1)
        assert len(markup.inline_keyboard) == 2


# ─────────────────────────────────────────────────────────────────────────────
# build_swarm_team_buttons
# ─────────────────────────────────────────────────────────────────────────────


class TestBuildSwarmTeamButtons:
    def test_returns_inline_keyboard_markup(self):
        markup = build_swarm_team_buttons()
        assert isinstance(markup, InlineKeyboardMarkup)

    def test_four_teams(self):
        markup = build_swarm_team_buttons()
        all_buttons = [btn for row in markup.inline_keyboard for btn in row]
        assert len(all_buttons) == 4

    def test_traders_button_exists(self):
        markup = build_swarm_team_buttons()
        all_cbs = [btn.callback_data for row in markup.inline_keyboard for btn in row]
        assert "action:swarm_team:traders" in all_cbs

    def test_coders_button_exists(self):
        markup = build_swarm_team_buttons()
        all_cbs = [btn.callback_data for row in markup.inline_keyboard for btn in row]
        assert "action:swarm_team:coders" in all_cbs

    def test_analysts_button_exists(self):
        markup = build_swarm_team_buttons()
        all_cbs = [btn.callback_data for row in markup.inline_keyboard for btn in row]
        assert "action:swarm_team:analysts" in all_cbs

    def test_creative_button_exists(self):
        markup = build_swarm_team_buttons()
        all_cbs = [btn.callback_data for row in markup.inline_keyboard for btn in row]
        assert "action:swarm_team:creative" in all_cbs

    def test_each_team_in_own_row(self):
        markup = build_swarm_team_buttons()
        assert len(markup.inline_keyboard) == 4

    def test_all_callbacks_start_with_action(self):
        markup = build_swarm_team_buttons()
        for row in markup.inline_keyboard:
            for btn in row:
                assert btn.callback_data.startswith("action:")


# ─────────────────────────────────────────────────────────────────────────────
# build_costs_detail_buttons
# ─────────────────────────────────────────────────────────────────────────────


class TestBuildCostsDetailButtons:
    def test_returns_inline_keyboard_markup(self):
        markup = build_costs_detail_buttons()
        assert isinstance(markup, InlineKeyboardMarkup)

    def test_single_button(self):
        markup = build_costs_detail_buttons()
        all_buttons = [btn for row in markup.inline_keyboard for btn in row]
        assert len(all_buttons) == 1

    def test_callback_data(self):
        markup = build_costs_detail_buttons()
        btn = markup.inline_keyboard[0][0]
        assert btn.callback_data == "action:costs_detail"

    def test_button_text_contains_keyword(self):
        markup = build_costs_detail_buttons()
        btn = markup.inline_keyboard[0][0]
        assert "моделям" in btn.text.lower()


# ─────────────────────────────────────────────────────────────────────────────
# build_health_recheck_buttons
# ─────────────────────────────────────────────────────────────────────────────


class TestBuildHealthRecheckButtons:
    def test_returns_inline_keyboard_markup(self):
        markup = build_health_recheck_buttons()
        assert isinstance(markup, InlineKeyboardMarkup)

    def test_single_button(self):
        markup = build_health_recheck_buttons()
        all_buttons = [btn for row in markup.inline_keyboard for btn in row]
        assert len(all_buttons) == 1

    def test_callback_data(self):
        markup = build_health_recheck_buttons()
        btn = markup.inline_keyboard[0][0]
        assert btn.callback_data == "action:health_recheck"

    def test_button_text_contains_keyword(self):
        markup = build_health_recheck_buttons()
        btn = markup.inline_keyboard[0][0]
        assert "проверить" in btn.text.lower()
