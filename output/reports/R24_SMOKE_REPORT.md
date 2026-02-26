# R24 SMOKE REPORT
Generated at: 2026-02-25 03:27:24

## Overall Status: ✅ PASS

### Summary
- **routing_smoke.py**: OK
- **cloud_tier_smoke.py**: OK
- **runtime_snapshot.py**: OK

### Details
#### routing_smoke.py
Exit Code: 0
**Stdout:**
```
🔍 Проверка Health: http://127.0.0.1:8080/api/health
✅ Health OK (200)
🔍 Проверка Stats: http://127.0.0.1:8080/api/stats
✅ Stats OK (200)
🔍 Проверка EcoHealth: http://127.0.0.1:8080/api/ecosystem/health
✅ EcoHealth OK (200)

✨ Все эндпоинты роутинга доступны.

```
---
#### cloud_tier_smoke.py
Exit Code: 0
**Stdout:**
```
🔍 Валидация Cloud Tier и Force Mode...
ℹ️ Текущий режим: auto
ℹ️ Активный тир: free
✅ Инварианты Cloud Tier в норме.

```
---
#### runtime_snapshot.py
Exit Code: 0
**Stdout:**
```
📸 Сбор снимка рантайма...
✅ Снимок сохранен в temp/runtime_snapshot.json

```
---
