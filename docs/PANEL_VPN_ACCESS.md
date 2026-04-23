# Доступ к Krab Panel (`:8080`) через VPN (3x-ui / Xray)

## Почему "просто включить VPN" не работает

Xray VLESS создаёт туннель: **iPhone → VPN-сервер → интернет**.
`localhost:8080` на Mac — это локальный адрес Mac, VPN-сервер о нём не знает.
Чтобы iPhone через VPN достучался до панели на Mac, нужен **reverse tunnel**:
Mac постоянно пробрасывает свой порт 8080 на VPN-сервер, а nginx на сервере
выдаёт его наружу (только для VPN-клиентов).

---

## Архитектура решения

```
iPhone (VPN клиент)
  └─► VPN-сервер :443 (VLESS / Xray)
        └─► nginx :18080 (только VPN-адрес)
              └─► 127.0.0.1:18080 (reverse SSH tunnel)
                    └─► Mac localhost:8080 (Krab panel)
```

Соединение Mac→VPN-сервер держит reverse SSH tunnel (LaunchAgent).
nginx на сервере проксирует `:18080 → 127.0.0.1:18080` с basic auth.

---

## Шаг 1 — Поднять nginx на VPN-сервере

Добавьте nginx сайдкар в `/Users/pablito/Antigravity_AGENTS/VPN/docker-compose.yml`:

```yaml
  krab-panel-proxy:
    image: nginx:alpine
    container_name: krab-panel-proxy
    restart: always
    network_mode: host          # нужен host-сеть, чтобы слушать на VPN IP
    volumes:
      - ./nginx-krab.conf:/etc/nginx/conf.d/krab.conf:ro
      - ./htpasswd:/etc/nginx/.htpasswd:ro
    depends_on:
      - vpn-panel
```

Создайте `nginx-krab.conf` в папке VPN:

```nginx
server {
    # Слушаем только на loopback — reverse SSH tunnel пробрасывает сюда
    listen 127.0.0.1:18080;

    auth_basic            "Krab Panel";
    auth_basic_user_file  /etc/nginx/.htpasswd;

    location / {
        proxy_pass         http://127.0.0.1:8080;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_read_timeout 120s;
    }
}
```

> **Примечание**: nginx слушает только на `127.0.0.1:18080`, не на публичном IP.
> iPhone получит доступ только когда активен reverse SSH tunnel с Mac.

Создайте htpasswd-файл (один раз, на Mac):

```bash
# Установить htpasswd если нет: brew install httpd
htpasswd -c /Users/pablito/Antigravity_AGENTS/VPN/htpasswd krab
# Введите пароль. Этот файл монтируется в контейнер.
```

Примените изменения:

```bash
cd /Users/pablito/Antigravity_AGENTS/VPN
./apply_compose_changes.command
```

---

## Шаг 2 — Reverse SSH tunnel с Mac на VPN-сервер

Mac должен постоянно держать туннель: `localhost:8080 → VPN-server:127.0.0.1:18080`.

### 2a. Разовый тест (в терминале)

```bash
ssh -N -R 18080:localhost:8080 root@<VPN_SERVER_IP>
# Ctrl+C для остановки
```

После этого с iPhone (через VPN) откройте: `http://<VPN_SERVER_IP>:18080`
(пока nginx слушает только на 127.0.0.1, это не сработает — нужен шаг ниже)

> На VPN-сервере убедитесь, что в `/etc/ssh/sshd_config` разрешено:
> ```
> GatewayPorts clientspecified
> ```
> Или проще — nginx слушает `127.0.0.1:18080`, а SSH форвардит туда же.
> В этом случае `GatewayPorts` не нужен.

### 2b. Постоянный туннель через LaunchAgent на Mac

Создайте `~/Library/LaunchAgents/ai.krab.panel-tunnel.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>ai.krab.panel-tunnel</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/ssh</string>
    <string>-N</string>
    <string>-o</string><string>ServerAliveInterval=30</string>
    <string>-o</string><string>ServerAliveCountMax=3</string>
    <string>-o</string><string>ExitOnForwardFailure=yes</string>
    <string>-o</string><string>StrictHostKeyChecking=no</string>
    <string>-R</string><string>127.0.0.1:18080:localhost:8080</string>
    <string>root@YOUR_VPN_SERVER_IP</string>
  </array>
  <key>KeepAlive</key>
  <true/>
  <key>RunAtLoad</key>
  <true/>
  <key>StandardErrorPath</key>
  <string>/tmp/krab-panel-tunnel.log</string>
</dict>
</plist>
```

Замените `YOUR_VPN_SERVER_IP` на реальный IP вашего VPN-сервера.

Активация:

```bash
launchctl load ~/Library/LaunchAgents/ai.krab.panel-tunnel.plist
# Проверка:
launchctl list | grep krab.panel-tunnel
# Логи:
tail -f /tmp/krab-panel-tunnel.log
```

---

## Шаг 3 — Открыть порт в nginx для VPN-клиентов

iPhone через VPN получает адрес VPN-сервера. Нужно чтобы nginx слушал на
интерфейсе VPN-сервера, доступном клиентам. Обновите `nginx-krab.conf`:

```nginx
server {
    # 127.0.0.1 — только для SSH tunnel forward
    listen 127.0.0.1:18080;
    # Добавьте публичный IP сервера (или 0.0.0.0) — но тогда порт открыт всем!
    # Лучше оставить 127.0.0.1 и использовать Xray port forwarding (см. ниже).
    ...
}
```

### Альтернатива: Xray dokodemo-door (рекомендуется)

Вместо открытия порта наружу добавьте в 3x-ui новый inbound типа **Dokodemo-door**:
- Протокол: `dokodemo-door`
- Порт: любой, например `19080`
- Адрес: `127.0.0.1`
- Порт назначения: `18080`
- Network: `tcp`

Тогда с iPhone (через VPN) открывайте: `http://<VPN_SERVER_IP>:19080`
Это proxies через VPN-сервер → nginx → SSH tunnel → Krab panel.

Или проще: в `nginx-krab.conf` добавьте listen на loopback, а в Xray настройте
forwarding. Это более чистое решение без открытия порта на публичный интерфейс.

---

## Шаг 4 — Безопасность

### Basic auth (nginx)

Уже настроен через htpasswd выше. Логин/пароль запрашивается при каждом открытии.

### Дополнительный слой: WEB_API_KEY в Krab

В `.env` (или окружении при запуске):

```bash
WEB_API_KEY=your-secret-key-here
```

При установленном `WEB_API_KEY` все write-операции через панель требуют
заголовок `X-Krab-Web-Key: your-secret-key-here`.

### PANEL_BASIC_AUTH (встроенный в Krab)

Для полного read+write auth без nginx можно использовать встроенный middleware
(добавлен в `src/modules/web_app.py` — см. ниже). Установите:

```bash
PANEL_BASIC_AUTH=username:password
```

Панель будет требовать HTTP Basic Auth на всех эндпоинтах.
Исключения: `/api/health/lite` (для watchdog/мониторинга).

---

## Тест соединения

1. Подключите iPhone к VPN (VLESS через 3x-ui)
2. Откройте Safari: `http://<VPN_SERVER_IP>:19080` (или порт dokodemo-door)
3. Введите логин/пароль из htpasswd
4. Должна открыться Krab Owner Panel

### Диагностика

```bash
# На Mac — проверить туннель:
launchctl list | grep krab.panel-tunnel
curl -v http://localhost:8080/api/health/lite

# На VPN-сервере — проверить что туннель пришёл:
ss -tlnp | grep 18080
curl http://127.0.0.1:18080/api/health/lite

# Логи туннеля на Mac:
tail /tmp/krab-panel-tunnel.log
```

---

## Итого: что нужно сделать

| Шаг | Где | Что |
|-----|-----|-----|
| 1 | VPN-сервер | Добавить nginx sidecar в docker-compose |
| 2 | VPN-сервер | Создать `nginx-krab.conf` + `htpasswd` |
| 3 | Mac | Создать LaunchAgent для reverse SSH tunnel |
| 4 | 3x-ui панель | Добавить dokodemo-door inbound (порт 19080) |
| 5 | iPhone | Подключиться к VPN, открыть `http://server:19080` |
| 6 | Опционально | Установить `PANEL_BASIC_AUTH=user:pass` в Krab env |

---

## Альтернатива без reverse tunnel: WireGuard mesh

Если нужен постоянный двусторонний доступ (Mac ↔ iPhone напрямую),
рассмотрите добавление WireGuard mesh-сети поверх существующего 3x-ui.
Это сложнее в настройке, но надёжнее для long-lived сессий.
