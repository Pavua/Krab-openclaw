# Release Readiness Matrix

## Допустимые статусы

| Статус | Когда использовать |
| --- | --- |
| `release-ready` | обязательные gate/checks пройдены, evidence свежий, остаточные риски не блокируют |
| `release-blocked` | есть кодовый, тестовый или runtime blocker |
| `environment-blocked` | среда мешает дать честный verdict |
| `helper-verified, final pablito pass required` | часть проверок зелёная, но финальный критичный проход не сделан на основной учётке |

## Минимальные сигналы для сильного verdict

- свежий `pre_release_smoke_latest.json` или timestamped run;
- свежий `r20_merge_gate_latest.json`;
- отсутствие stale helper-only подмены;
- понятный owner следующего шага.
