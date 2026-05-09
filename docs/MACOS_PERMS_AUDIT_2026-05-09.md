# macOS TCC Permissions Audit — Wave 44-R-perms (2026-05-09)

Аудит macOS Privacy & Security разрешений для Krab core, codex-cli и
зависимостей. Источник истины — system TCC.db (`/Library/Application
Support/com.apple.TCC/TCC.db`, читается без sudo на этой машине) и user
TCC.db (`~/Library/Application Support/com.apple.TCC/TCC.db`).

`auth_value` коды: `0=denied`, `2=allowed`, `3=limited`, `4=allowed (via
prompt)`, `5=allowed (per-component)`.

## Контекст исполнения

Krab core (`KraabUserbot`) запускается через `new start_krab.command` →
launchd → `~/Library/LaunchAgents/ai.krab.*.plist` → бинарь:

```
/Users/pablito/Antigravity_AGENTS/Краб/venv/bin/python
  → symlink → /opt/homebrew/opt/python@3.13/bin/python3.13
  → real    → /opt/homebrew/Cellar/python@3.13/3.13.12_1/Frameworks/Python.framework/Versions/3.13/bin/python3.13
```

TCC видит **реальный путь** (после resolve symlinks), поэтому все права надо
выдавать на `python@3.13/3.13.12_1/.../python3.13` (drag-and-drop сам
поймает реальный путь).

`openclaw gateway` — ребёнок Python-процесса (`/opt/homebrew/bin/openclaw` =
node-shebang script). У бинаря **нет ни одной TCC-записи** — он работает
под "responsible-process" родителя (Python). То есть права на Python
покрывают и openclaw.

`codex` — node-script, но Apple даёт ему bundle id `com.openai.codex`
(подписан как app), и он живёт в TCC отдельно — у него **полный набор**
разрешений (FDA, Screen Recording, Accessibility, ListenEvent).

Hammerspoon **на машине не установлен** (`/Applications/Hammerspoon.app`
отсутствует, `~/.hammerspoon/init.lua` есть, но без бинаря — это легаси).
В CLAUDE.md упомянут порт 8013 `mcp-hammerspoon`, но фактически relay не
запущен. **Действий не требуется** — но если планируется вернуть
Hammerspoon, ему понадобится Accessibility + ScreenCapture + AppleEvents.

## Сводка прав

### 1. Krab venv Python (`/opt/homebrew/Cellar/python@3.13/.../python3.13`)

| Service | auth | Status | Required | Action |
|---|---|---|---|---|
| Accessibility | 2 | ALLOWED | да (Hammerspoon-bridge, automacOS) | OK |
| AppleEvents | 2 | ALLOWED (multi-target) | да | OK |
| Calendar | 4 | ALLOWED | да (`mcp__krab__calendar_*`) | OK |
| Reminders | 2 | ALLOWED | да (`mcp__krab__reminders_*`) | OK |
| Photos | 2 | ALLOWED | optional | OK |
| Documents/Desktop/Downloads | 2 | ALLOWED | да | OK |
| MediaLibrary | 2 | ALLOWED | optional | OK |
| **ScreenCapture** | — | **MISSING** | **да** (Playwright/peekaboo/`screencapture`) | **GRANT** |
| **SystemPolicyAllFiles** (FDA) | — | **MISSING** | **рекомендуется** (read user files outside Documents/Desktop/Downloads, `~/.openclaw`, archive.db) | **GRANT** |
| **ListenEvent** | — | MISSING | optional (нет hotkey-listening сейчас) | skip |
| **PostEvent** | — | MISSING | optional (если нужен programmatic input) | skip |
| **Microphone** | — | MISSING | нет (Krab Ear отдельно) | skip |
| **SpeechRecognition** | — | MISSING | нет | skip |

**Симптомы из логов** (`~/.openclaw/krab_runtime_state/krab_main.log`):
- `2026-05-09 01:18`: *"could not create image from display"* — это
  exact failure mode при отсутствии **Screen Recording** для процесса.
- `2026-05-09 21:24`: *"macOS permission MachPortRendezvousServer"* —
  тот же самый Screen Recording denial (CGRequestScreenCaptureAccess).
- `2026-05-06 20:46`: *"operation not permitted к OrbStack socket"* —
  не TCC, а Unix-permission на сокет, в этом аудите не фиксится.

### 2. openclaw (`/opt/homebrew/bin/openclaw`)

Нет TCC-записей. Права наследуются от родителя (Python). **Действий не
требуется.**

### 3. codex (`com.openai.codex`)

Полный комплект: AppleEvents, Calendar, Reminders, Microphone,
Accessibility (system-DB), ListenEvent, ScreenCapture, FDA. **Действий
не требуется.**

### 4. Krab Ear (`com.antigravity.krab-ear`, `KrabEarAgent` binaries)

| Service | auth | Status |
|---|---|---|
| Accessibility | 2 | OK |
| Microphone | 2 | OK |
| ScreenCapture | 2 | OK |
| **SystemPolicyAllFiles** | **0** | **DENIED** ⚠️ |

`com.antigravity.krab-ear` имеет **explicit deny** на FDA. Если KrabEar
нужен доступ к произвольным файлам (e.g. чтение transcript-логов из
`~/.openclaw`), это надо снять. На текущий момент функционал работает
(транскрипты пишутся в свой sandbox), так что это **observation, не
блокер**.

### 5. Прочее (для справки)

| Bundle | Что есть |
|---|---|
| com.apple.Terminal | AppleEvents, Accessibility, ScreenCapture, FDA — полный |
| com.anthropic.claude-code | Accessibility, ScreenCapture, FDA — полный |
| /opt/homebrew/Cellar/node/* | различные (по версиям) |
| /usr/bin/osascript | AppleEvents=2, PostEvent=2 |

## Top 5 missing perms + remediation

1. **ScreenCapture для Krab Python** — критично, активно ломает
   screencapture/Playwright. → System Settings.
2. **SystemPolicyAllFiles (Full Disk Access) для Krab Python** —
   рекомендуется для чтения произвольных user-файлов. → System Settings.
3. *(optional)* **PostEvent для Krab Python** — если нужен programmatic
   keyboard input через CGEventPost. → System Settings.
4. *(optional)* **ListenEvent для Krab Python** — если нужен global
   hotkey listening. → System Settings.
5. **FDA для Krab Ear** (snять deny=0) — не блокер, но косметика.
   → System Settings (раскрыть toggle и включить).

## Remediation steps (ручная процедура)

> **TCC.db нельзя править напрямую** — SIP запрещает запись в
> `/Library/Application Support/com.apple.TCC/`. `tccutil reset` сбросит
> ВСЕ грани для service у ВСЕХ клиентов — **не использовать**. Только GUI
> или drag-drop в System Settings.

### Шаг 1: Screen Recording для Krab Python

1. Открыть **System Settings → Privacy & Security → Screen & System
   Audio Recording** (на Sequoia/Tahoe — переименовано из "Screen
   Recording"; возможен также under-name "Screen Recording").
2. Нажать `+` (потребуется Touch ID / админ пароль).
3. В Finder нажать `Cmd+Shift+G` и вставить точный путь:
   ```
   /opt/homebrew/Cellar/python@3.13/3.13.12_1/Frameworks/Python.framework/Versions/3.13/bin/python3.13
   ```
4. Выбрать `python3.13` → Open. Toggle включится автоматически.
5. **Перезапустить Krab**: `new Stop Krab.command` → подождать → `new
   start_krab.command`. (TCC решения кешируются per-process, рестарт
   обязателен.)

### Шаг 2: Full Disk Access для Krab Python

1. **System Settings → Privacy & Security → Full Disk Access**.
2. `+`, тот же путь:
   `/opt/homebrew/Cellar/python@3.13/3.13.12_1/Frameworks/Python.framework/Versions/3.13/bin/python3.13`.
3. Перезапустить Krab.

### Шаг 3 (optional): PostEvent + ListenEvent для Krab Python

В Sequoia это объединено в **Accessibility** (которое уже granted) +
**Input Monitoring** (если появятся features требующие global hotkeys).
Сейчас Krab их не использует — **skip пока не понадобится**.

### Шаг 4 (косметика): KrabEar FDA

1. **System Settings → Privacy & Security → Full Disk Access**.
2. Найти **Krab Ear** в списке (toggle = OFF).
3. Включить toggle. Если не работает — удалить запись `−`, добавить
   через `+` → `/Applications/Krab Ear.app`.

## Что НЕ делалось (и почему)

- `tccutil reset <SERVICE> <bundle_id>` — НЕ запускалось. Ресет один
  service для одного клиента *в теории* безопасен, но на практике
  Apple-документация описывает поведение как "reset for all" для
  старых сборок, и я не хочу терять Accessibility/AppleEvents у Python.
  Только GUI-add.
- Прямая запись в TCC.db — запрещена SIP, нужен csrutil disable + boot
  в Recovery. Не вариант.
- AppleScript/osascript автоматизация для programmatic add — Apple
  закрыли этот вектор в Catalina. Только User Approval через GUI.

## Проверка после применения

```bash
# Screen Recording для Krab Python (должен вернуть auth_value=2)
sqlite3 /Library/Application\ Support/com.apple.TCC/TCC.db \
  "SELECT auth_value FROM access WHERE service='kTCCServiceScreenCapture' \
   AND client LIKE '%python@3.13%'"

# FDA для Krab Python
sqlite3 /Library/Application\ Support/com.apple.TCC/TCC.db \
  "SELECT auth_value FROM access WHERE service='kTCCServiceSystemPolicyAllFiles' \
   AND client LIKE '%python@3.13%'"

# Live test: после Krab restart — попросить в Telegram сделать screenshot,
# не должно быть "could not create image from display"
```

## Бинари / bundle IDs для drag-drop

| Что | Путь |
|---|---|
| Krab venv Python (Screen Recording, FDA) | `/opt/homebrew/Cellar/python@3.13/3.13.12_1/Frameworks/Python.framework/Versions/3.13/bin/python3.13` |
| Krab Ear (FDA — снять deny) | `/Applications/Krab Ear.app` (= `/Users/pablito/Antigravity_AGENTS/Krab Ear/Krab Ear.app`) |
| openclaw (НЕ ТРОГАТЬ) | `/opt/homebrew/bin/openclaw` — наследует от Python |
| codex (already configured) | `com.openai.codex` |

## Заключение

**Единственная активно ломающаяся проблема — Screen Recording для Krab
Python.** Лог-сигнал чёткий ("could not create image from display"
2026-05-09 01:18). После grant + restart screencapture/Playwright должны
снова работать.

FDA — рекомендация. Без неё Krab всё ещё работает, но при попытке
прочитать файл из `~/Library/Mail`, `~/.ssh`, `/private/var/db/`
получит EPERM.

Остальное — observations и optional hardening.
