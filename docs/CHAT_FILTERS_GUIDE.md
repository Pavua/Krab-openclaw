# Chat Filters — гид пользователя

Как Krab решает, в каких чатах отвечать, и как этим управлять из Telegram.

## Что это

Chat filter — per-chat режим, который определяет, реагирует ли Краб на сообщения
в конкретном чате. Позволяет держать один userbot-аккаунт в десятках групп без
спама: везде — `mention-only` по умолчанию, в рабочих DM — `active`, в шумных
каналах с автопостингом — `muted`.

Backend: `src/core/chat_filter_config.py` (singleton `chat_filter_config`).
State: `~/.openclaw/krab_runtime_state/chat_filters.json`.

## Три режима

| Mode | Поведение | Дефолт для |
|------|-----------|------------|
| `active` | Отвечает на все сообщения | DM (личные чаты) |
| `mention-only` | Отвечает только на `@krab`, упоминание «Краб», или reply на его сообщение | Group / supergroup |
| `muted` | Полное игнорирование — ноль ответов | — |

Дефолт выбирается автоматически по типу чата. Явное правило (сохранённое
командой `!listen`) всегда перекрывает дефолт.

## Команды

Все команды работают в текущем чате (`chat_id` подставляется автоматически):

```
!listen                — показать текущий режим текущего чата
!listen active         — реагировать на все сообщения
!listen mention-only   — только @mention / «Краб» / reply
!listen muted          — полное молчание
!listen reset          — удалить явное правило → вернуться к дефолту
!listen reload         — перечитать chat_filters.json с диска
!listen list           — все чаты с явными правилами (owner-only)
!listen stats          — статистика по режимам (owner-only)
```

`!mode` — alias для `!listen` (тот же обработчик).

Примеры:

```
!listen mention-only
!listen muted
!listen reset
```

## Hot-reload

Config читается лениво: при каждом `get_mode()` / `set_mode()` Краб проверяет
`mtime` файла `chat_filters.json`. Если файл изменился внешне (ручной edit,
sync из другого инстанса, миграция) — правила перезагружаются **без рестарта
Краба**.

- Порог срабатывания: разница mtime > 0.1 сек.
- Лог-событие: `chat_filter_hot_reload` со старым и новым mtime.
- Принудительная перезагрузка: `!listen reload` — возвращает, изменился ли
  набор правил.

Это значит: можно редактировать JSON в редакторе и изменения подхватятся на
следующем же сообщении, без даунтайма.

## Формат конфига

`~/.openclaw/krab_runtime_state/chat_filters.json` — плоский объект
`chat_id → rule`:

```json
{
  "-1001234567890": {
    "mode": "muted",
    "updated_at": 1744934400.0,
    "note": "шумный канал с автопостингом"
  },
  "123456789": {
    "mode": "active",
    "updated_at": 1744934500.0,
    "note": ""
  }
}
```

Поля:

- `mode` — один из `active`, `mention-only`, `muted`. Другие значения
  отвергаются (`ValueError`).
- `updated_at` — unix-timestamp последнего изменения.
- `note` — свободный текст (необязательный).

Чаты, которых нет в конфиге, используют дефолт по типу (DM → `active`,
группа → `mention-only`).

## Edge cases

- **Forum Topics.** У форум-группы один `chat_id` — все топики наследуют
  режим группы. Отдельный режим per-topic не поддерживается.
- **Bot-команды (`!xxx`).** Команды владельца всегда bypass-ят фильтр —
  `!listen`, `!model`, `!stats` и т.п. срабатывают даже в `muted` чате.
  Иначе было бы невозможно выйти из `muted`.
- **Swarm-топики.** Топики свёрма (`🐝 Krab Swarm`) всегда работают как
  `active` — иначе live-broadcast и intervention сломаются.
- **Неизвестный chat_id.** Если файл содержит строку-ключ, которая не парсится
  как int, правило всё равно применяется — сравнение идёт через `str(chat_id)`.

## Troubleshooting

**Краб не отвечает в группе.**
1. Выполни `!listen` — увидишь текущий режим.
2. Если `mention-only` и ты ждал реакции на обычное сообщение — это by design.
   Упомяни `@krab` / «Краб» или ответь reply на его сообщение.
3. Если `muted` — `!listen active` или `!listen mention-only`.

**Отредактировал `chat_filters.json` вручную, изменения не видны.**
- Подожди следующего сообщения — hot-reload сработает на первом же
  `get_mode()` (~ мгновенно).
- Форсированно: `!listen reload`.

**Потерял список правил.**
- `!listen list` — покажет все чаты с явными правилами.
- `!listen stats` — агрегат по режимам.

**`ValueError: invalid mode`.**
- Проверь правописание. Валидны только `active`, `mention-only`, `muted`.
  `mention_only` (с underscore) не принимается.

## Tips & patterns

- **Большая группа с малым сигналом.** Default `mention-only` уже правильный —
  ничего не делай.
- **Рабочий DM, где Краб — ассистент.** Убедись, что режим `active` (дефолт
  для DM). Можно явно закрепить `!listen active`, чтобы не зависеть от
  эвристики `is_group`.
- **Чат с автопостингом / ботом-бродкастером.** `!listen muted` — сэкономит
  токены и избавит от случайных ответов на шум.
- **Временная тишина на встречу.** `!listen muted`, после — `!listen reset`
  (вернёт дефолт, а не жёсткое `active`).
- **Миграция правил между машинами.** Просто скопируй
  `~/.openclaw/krab_runtime_state/chat_filters.json` — hot-reload подхватит
  без рестарта.

## См. также

- Код: `src/core/chat_filter_config.py`
- Команды: `src/handlers/command_handlers.py` (искать `!listen`)
- Тесты: `tests/unit/test_chat_filter_config.py`
