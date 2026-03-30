# Conflict Matrix

## Безопасные параллельные комбинации

| Lane A | Lane B | Условие |
|---|---|---|
| Backend code | Docs update | docs пишутся после code freeze или по подтверждённому diff |
| Runtime code | Unit tests | тестовый lane не меняет production файлы |
| Owner UI | Browser smoke | smoke lane только читает и проверяет |
| Feature lane | Read-only review lane | reviewer не правит те же файлы |

## Комбинации только в serial mode

| Ситуация | Почему |
|---|---|
| Два агента хотят менять один `src/*.py` | гарантированный write-conflict |
| Две учётки запускают live runtime | конфликт ownership и state |
| Один агент правит launcher, второй одновременно меняет тот же workflow в docs | docs быстро устареют |
| Несколько lane пишут в один handoff документ | теряется truthful chronology |

## Минимальная карточка lane

- `owner`
- `goal`
- `write_scope`
- `read_only_scope`
- `verification`
- `artifact`
- `merge_after`
