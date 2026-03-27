# Telegram Owner E2E: второй аккаунт `p0lrd`

Дата: 2026-03-27
Контур: `Yung Nagato` (userbot) <-live-> `p0lrd` (второй Telegram MCP)

## Что проверено

### 1. Private inbound explicit trigger
- От `p0lrd` в личку `@yung_nagato` отправлены триггеры:
  - `PRIVATEP0-FIX-20260327-1823`
  - `PRIVATEP0-FIXVERIFY-20260327-1822`
- Userbot склеил их в один private burst и зафиксировал persisted inbox item:
  - `incoming:312322764:11432`
- Workflow:
  - `created` -> `background_started` -> `reply_sent`
- Доставка:
  - текст: message `11434`
  - voice: message `11435`

Фактический текст ответа:

```text
PRIVATEP0-FIX-20260327-1823. Исправления применены, Краб в строю! 🦀

PRIVATEP0-FIXVERIFY-20260327-1822. Проверка пройдена, всё работает как часы. 🦀
```

### 2. Group mention / owner mention
- В группе `YMB FAMILY FOREVER` (`-1001804661353`) от `p0lrd` отправлены:
  - `GROUPP0-TRIGGER-20260327-1826`
  - `GROUPP0-20260327-1824`
- Persisted inbox items:
  - `incoming:-1001804661353:764818`
  - `incoming:-1001804661353:764820`
- Для второго group-запроса userbot честно показал queue handoff:
  - message `764821`
  - текст содержит: `Новый запрос поставлен сразу за ней.`

Итоговая доставка:
- для `764818`: text message `764822`, voice `764823`
- для `764820`: text message `764824`, voice `764825`

Фактический текст финального успешного group-ответа:

```text
GROUPP0-20260327-1824 🦀 Краб на связи: запрос принят, всё чётко и по делу.
```

## Вывод
- Private owner inbound через второй аккаунт подтверждён end-to-end.
- Group mention / owner mention подтверждён end-to-end.
- Background handoff теперь не оставляет новые owner-запросы в `open`, даже если в чате уже есть активная фоновая задача.
- Voice-доставка сохранилась и в private, и в group flow.
