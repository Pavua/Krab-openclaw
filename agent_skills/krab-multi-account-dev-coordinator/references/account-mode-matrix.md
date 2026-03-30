# Account Mode Matrix

## Режимы

| Режим | Кто обычно владелец | Что разрешено | Что запрещено | Чем заканчивается |
|---|---|---|---|---|
| `code-only helper` | `USER2` или `USER3` | код, тесты, docs, локальные проверки | запускать второй live runtime поверх чужого | commit или diff плюс truthful summary |
| `controlled live helper` | helper account после reclaim или freeze | ограниченный live smoke, bootstrap, relogin | считать результат финальным релизным verdict | свежий handoff bundle |
| `return-to-pablito` | `pablito` | final acceptance, release gate, merge verdict | опираться только на старый helper-state | финальная acceptance evidence |

## Проверки перед live cycle

- `Check New Account Readiness.command`
- `Check Current Account Runtime.command`
- `Check Shared Repo Drift.command`, если есть сомнение по branch или HEAD
- свежий `git status --short --branch`

## Что никогда не шарить

- `~/.openclaw/*`
- browser profile и remote debugging state
- OAuth или session state
- `.env` и account-local secrets
