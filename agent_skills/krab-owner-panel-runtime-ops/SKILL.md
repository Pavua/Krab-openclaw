---
name: krab-owner-panel-runtime-ops
description: "Использовать owner-oriented web panel Краба на `:8080` как операционный контур для runtime, ACL, Browser Relay и model routing. Применять, когда нужно подтвердить, что UI не врёт о состоянии runtime, проверить write-операции панели, воспроизвести баг в owner panel или провести живую acceptance-проверку интерфейса после backend-изменений."
---

# Krab Owner Panel Runtime Ops

Используй этот навык, когда owner panel на `:8080` выступает не как витрина, а как реальный операционный интерфейс. Каждую write-операцию подтверждай runtime-side эффектом.

## Предусловие

```bash
cd /Users/pablito/Antigravity_AGENTS/Краб
```

## Основные сценарии

- truthful runtime status
- `Userbot ACL`
- `Browser / MCP Readiness`
- model autoswitch / compat probe
- runtime repair endpoints

## Рабочий цикл

1. Подтвердить, что backend панели поднят.
2. Открыть UI в браузере и снять исходный snapshot.
3. Пройти нужный сценарий реальными кликами.
4. Проверить DOM-изменение.
5. Подтвердить эффект через API или CLI.

## Ограничения

- Не считать визуальный ререндер доказательством изменения runtime.
- После write-операции проверять фактический backend-side state.
- Если UI показывает старый cached state, форсировать повторную проверку, а не принимать первое впечатление.
