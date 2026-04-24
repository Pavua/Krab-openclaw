# Named Cloudflare Tunnel — пошаговая настройка

> Переход с quick tunnel (`*.trycloudflare.com`, URL меняется при рестарте)
> на named tunnel (stable UUID + собственный hostname).
>
> **Зачем:** quick tunnels теряют 0–60 сек алёртов при каждом рестарте
> (пока `cf_tunnel_sync.sh` не обновит Sentry webhook), не имеют SLA и
> выглядят как `mirror-ignored-something.trycloudflare.com`.

---

## Текущее состояние (на момент написания)

- `cloudflared` установлен: `/opt/homebrew/bin/cloudflared`
- Папка `~/.cloudflared/` **пуста** — `cert.pem` отсутствует, т.е. **не authenticated**
- Активен quick tunnel через `scripts/launchagents/ai.krab.cloudflared-tunnel.plist`
- Self-heal: `scripts/cf_tunnel_sync.sh` + `ai.krab.cloudflared-sentry-sync.plist`

---

## Шаги (требуют пользователя)

### 1. Authenticate cloudflared (1 click в браузере)

```bash
cloudflared tunnel login
```

Откроется браузер → залогиниться в Cloudflare → выбрать зону (домен,
делегированный в CF; если домена нет — добавить Free zone и делегировать NS).
После успеха создастся `~/.cloudflared/cert.pem`.

**Почему этот шаг нельзя автоматизировать:** CF требует интерактивного OAuth.

### 2. Создать named tunnel

```bash
cloudflared tunnel create krab-alerts
```

Создаст:
- UUID туннеля (напр. `abc12345-...`)
- `~/.cloudflared/<UUID>.json` — credentials file

Сохранить UUID — понадобится в шаге 3.

### 3. Скопировать и отредактировать config

```bash
cp /Users/pablito/Antigravity_AGENTS/Краб/deploy/cloudflare/config.yml.template \
   ~/.cloudflared/config.yml
```

В файле заменить:
- `YOUR_TUNNEL_UUID_HERE` → реальный UUID (2 места)
- `krab.yourdomain.com` → желаемый hostname (напр. `krab-alerts.pavelrodionov.com`)

### 4. Привязать DNS

```bash
cloudflared tunnel route dns krab-alerts krab-alerts.yourdomain.com
```

Создаст CNAME `krab-alerts.yourdomain.com → <UUID>.cfargotunnel.com`.

### 5. Проверить локально

```bash
cloudflared tunnel run krab-alerts
# В другом окне:
curl -I https://krab-alerts.yourdomain.com/api/health/lite
```

Ctrl+C после подтверждения.

---

## Migration: quick → named без потери alerts

Sentry webhook должен переключиться на новый hostname **до** выключения quick
tunnel, иначе будет окно потерь. Алгоритм:

```bash
# 1. Убедиться что named tunnel работает параллельно с quick
#    (шаг 5 выше)

# 2. Руками обновить Sentry webhook на новый hostname
#    Sentry → Settings → Integrations → Internal Integration → Webhook URL
#    https://krab-alerts.yourdomain.com/api/sentry/webhook

# 3. Отправить тестовый alert через Sentry → проверить прибытие в Krab

# 4. Остановить quick tunnel и его self-heal
launchctl unload ~/Library/LaunchAgents/ai.krab.cloudflared-tunnel.plist
launchctl unload ~/Library/LaunchAgents/ai.krab.cloudflared-sentry-sync.plist

# 5. Включить named LaunchAgent
cp /Users/pablito/Antigravity_AGENTS/Краб/scripts/launchagents/ai.krab.cloudflared-named-tunnel.plist \
   ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/ai.krab.cloudflared-named-tunnel.plist

# 6. Проверить
launchctl list | grep cloudflared-named
tail -f /tmp/krab_cf_tunnel/named-tunnel.log
```

**Rollback** (если что-то сломалось):
```bash
launchctl unload ~/Library/LaunchAgents/ai.krab.cloudflared-named-tunnel.plist
launchctl load ~/Library/LaunchAgents/ai.krab.cloudflared-tunnel.plist
launchctl load ~/Library/LaunchAgents/ai.krab.cloudflared-sentry-sync.plist
# Вернуть старый webhook в Sentry (последний из cf_tunnel_sync.sh логов)
```

---

## Zero Trust Access Policy (рекомендуется)

Cloudflare Dashboard → Zero Trust → Access → Applications → Add Application:
- Type: Self-hosted
- Application domain: `krab-alerts.yourdomain.com`
- Policy: Allow → Emails → `pavelr7@gmail.com`
- Для Sentry webhook — отдельный path bypass:
  `krab-alerts.yourdomain.com/api/sentry/webhook` → Service Auth (token header)

---

## Что остаётся на self-heal после миграции

- `cf_tunnel_sync.sh` больше **не нужен** (URL стабильный)
- LaunchAgent `ai.krab.cloudflared-sentry-sync` можно удалить
- `KeepAlive=true` в named plist сам поднимет `cloudflared` при падении

---

## Связанные файлы

- `scripts/launchagents/ai.krab.cloudflared-named-tunnel.plist` — готовый LaunchAgent
- `deploy/cloudflare/config.yml.template` — шаблон конфига
- `docs/PANEL_EXPOSURE.md` — общий обзор вариантов внешнего доступа
- `scripts/cf_tunnel_sync.sh` — self-heal для quick tunnel (deprecated после миграции)
