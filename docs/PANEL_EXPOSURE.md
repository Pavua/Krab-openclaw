# Krab Panel — Варианты внешнего доступа

> Панель Krab по умолчанию слушает на `127.0.0.1:8080` (только localhost).
> Этот документ сравнивает три способа открыть к ней доступ извне.

---

## Сравнение вариантов

| Критерий | Option 1: VPN | Option 2: Cloudflare Tunnel ★ | Option 3: Caddy + Let's Encrypt |
|----------|--------------|-------------------------------|----------------------------------|
| Открытые порты на роутере | Нет | **Нет** | 443 |
| Зависимость от статического IP | Нет | Нет | **Да** |
| HTTPS из коробки | Нет (VPN шифрует) | **Да (Let's Encrypt auto)** | Да |
| Zero Trust access policy | Нет | **Да (Google/GitHub/email)** | Нет (basic auth) |
| Стоимость | Свой VPN-сервер | **Free tier** | Домен + сервер |
| Сложность настройки | Высокая | **Низкая** | Средняя |
| Приватность трафика | Максимальная | Трафик через CF edge | Только TLS |
| Готовность к мобильным | Требует VPN-клиента | **Любой браузер** | Любой браузер |
| Рекомендуется | Для параноиков | **Для большинства** | Если нет CF аккаунта |

---

## Option 1: VPN-only (самый приватный)

Полная инструкция: [`docs/PANEL_VPN_ACCESS.md`](./PANEL_VPN_ACCESS.md)

**Схема:**
```
iPhone → VPN (VLESS/Xray) → reverse SSH tunnel → Mac:8080
```

**Pros:**
- Нулевая экспозиция: ни один порт не открыт в интернет
- Трафик не проходит через третьи стороны
- Существующая инфраструктура (VPN-сервер уже есть)

**Cons:**
- Требует VPN-клиента на каждом устройстве
- Сложная цепочка: reverse tunnel + nginx + dokodemo-door
- Нет auto-HTTPS (только через nginx с самоподписанным сертификатом)
- Высокая латентность (два хопа: VPN → SSH tunnel)

**Быстрый старт:**
```bash
# Разовый тест reverse tunnel
ssh -N -R 127.0.0.1:18080:localhost:8080 root@<VPN_IP>
```

---

## Option 2: Cloudflare Tunnel ★ (рекомендуется)

Cloudflare Tunnel создаёт исходящее соединение с edge Cloudflare.
Никаких открытых портов, DNS A-record, или пробросов на роутере.
Access policy (Zero Trust) фильтрует по Google/GitHub/email перед тем,
как запрос дойдёт до Mac.

**Схема:**
```
Browser → CF Edge (HTTPS) → CF Tunnel agent → Mac:8080
                ↑
          Zero Trust Access Policy
          (Google/GitHub/email magic link)
```

**Pros:**
- Нет открытых портов
- Auto-HTTPS (Let's Encrypt через CF)
- Zero Trust access: только авторизованные email-адреса
- Free tier: до 50 пользователей, unlimited bandwidth
- Работает из любого браузера без клиента

**Cons:**
- Трафик проходит через CF edge (не end-to-end приватный)
- Требует CF аккаунт и домен, делегированный в CF
- При недоступности CF — панель недоступна

**Быстрый старт:**
```bash
# Установка
brew install cloudflared

# Авторизация (открывает браузер)
cloudflared tunnel login

# Создать туннель
cloudflared tunnel create krab-panel

# Посмотреть UUID туннеля
cloudflared tunnel list

# Скопировать конфиг
cp deploy/cloudflare/config.yml.template ~/.cloudflared/config.yml
# Отредактировать: вписать tunnel UUID и hostname

# Маршрут DNS (делегирует домен в CF)
cloudflared tunnel route dns krab-panel krab.yourdomain.com

# Запуск
cloudflared tunnel run krab-panel

# Или через интерактивный скрипт:
bash scripts/setup_cloudflare_tunnel.command
```

**Настройка Zero Trust Access Policy** (Cloudflare Dashboard):
1. Zero Trust → Access → Applications → Add Application
2. Self-hosted, URL: `krab.yourdomain.com`
3. Policy → Allow → Email → `pavelr7@gmail.com`
4. Identity providers: Google или GitHub

**LaunchAgent для автозапуска:**
```bash
cloudflared service install
# Устанавливает /Library/LaunchDaemons/com.cloudflare.cloudflared.plist
```

---

## Option 3: Caddy + Let's Encrypt (промежуточный)

Caddy автоматически получает TLS-сертификат от Let's Encrypt.
Требует: статический IP (есть), DNS A-record, открытый порт 443 на роутере.

**Pros:**
- Полный контроль (нет зависимости от CF)
- Auto-HTTPS (Caddy сам обновляет сертификат)
- Гибкая конфигурация (rate-limit, IP whitelist, etc.)

**Cons:**
- Нужен открытый порт 443 на роутере
- DNS A-record раскрывает статический IP (если домен публичный)
- Basic auth менее безопасен чем Zero Trust

**Установка:**
```bash
brew install caddy
```

**Caddyfile** (`~/.config/caddy/Caddyfile` или `/usr/local/etc/Caddyfile`):
```caddyfile
krab.yourdomain.com {
    # Basic auth (пароль захэшировать: caddy hash-password)
    basicauth /v4/* {
        pablito $2a$14$YOUR_BCRYPT_HASH_HERE
    }

    reverse_proxy 127.0.0.1:8080 {
        header_up X-Forwarded-For {remote_host}
        header_up X-Real-IP {remote_host}
    }

    # Ограничить логи (панель — приватный инструмент)
    log {
        output discard
    }
}
```

**DNS и роутер:**
```
1. DNS A-record: krab.yourdomain.com → <STATIC_IP>
2. Роутер: порт 443 → Mac (внутренний IP)
3. caddy start --config /usr/local/etc/Caddyfile
```

**Хэш пароля для Caddyfile:**
```bash
caddy hash-password --plaintext "your-password"
# Вставьте вывод в Caddyfile вместо $2a$14$...
```

---

## Встроенный basic auth Krab (safety net для всех вариантов)

Независимо от выбранного варианта, можно включить bcrypt-auth прямо в FastAPI:

```bash
# Включить
export KRAB_PANEL_AUTH=1
export KRAB_PANEL_USERNAME=pablito
export KRAB_PANEL_PASSWORD_HASH=$(python -c "import bcrypt; print(bcrypt.hashpw(b'yourpass', bcrypt.gensalt()).decode())")

# Или через команду в Telegram (owner-only):
# !setpanelauth pablito yourpassword
```

**Переменные окружения:**

| Переменная | Описание |
|------------|----------|
| `KRAB_PANEL_AUTH` | `1` = включить bcrypt auth |
| `KRAB_PANEL_USERNAME` | Логин для Basic Auth |
| `KRAB_PANEL_PASSWORD_HASH` | bcrypt-хэш пароля (`$2b$...`) |

Эндпоинты без auth (всегда доступны):
- `/api/health/lite` — watchdog/мониторинг
- `/api/v1/health` — версионированный health

---

## Рекомендованная комбинация

```
Option 2 (CF Tunnel) + KRAB_PANEL_AUTH=1
```

Двойная защита:
1. CF Zero Trust: только ваш email попадает к туннелю
2. Krab bcrypt auth: если CF политика обойдена — всё равно нужен пароль

---

---

## Migration: quick tunnel → named tunnel

Сейчас активен **quick tunnel** (`ai.krab.cloudflared-tunnel.plist`, URL
`*.trycloudflare.com`, меняется при рестарте). Недостатки: 0–60 сек потери
alerts на каждом switch, нет SLA, случайный URL.

**Готовые артефакты для перехода** (требуется 1 click у пользователя для
`cloudflared tunnel login`):
- Пошаговая инструкция: [`docs/NAMED_TUNNEL_SETUP.md`](./NAMED_TUNNEL_SETUP.md)
- LaunchAgent template: `scripts/launchagents/ai.krab.cloudflared-named-tunnel.plist`
- Config template: `deploy/cloudflare/config.yml.template`

Migration plan включает параллельный запуск quick + named, обновление
Sentry webhook до выключения quick, и rollback-сценарий.

---

## Связанные файлы

- `scripts/setup_cloudflare_tunnel.command` — интерактивный setup Option 2
- `deploy/cloudflare/config.yml.template` — шаблон конфига cloudflared
- `scripts/launchagents/ai.krab.cloudflared-named-tunnel.plist` — LaunchAgent для named tunnel
- `docs/NAMED_TUNNEL_SETUP.md` — пошаговая миграция quick → named
- `docs/PANEL_VPN_ACCESS.md` — полная инструкция Option 1
