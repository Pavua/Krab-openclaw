# Что Уже Известно И Обсуждалось — 21.03.2026

## Зачем этот файл

Этот файл нужен, чтобы новый агент не потерял не только факты, но и уже
пройденные развилки мышления.

## Уже известные факты

- USER2 и `pablito` работают с одним репозиторием, но не делят runtime/auth/browser state
- `Codex MCP` на USER2 подтверждён как usable
- `health/lite` truthful и показывает живой cloud route
- paid Google path подтверждён в runtime
- ordinary Chrome path и Debug browser path уже разведены как разные контуры

## Уже обсуждали и подтвердили

### 1. Baseline vs live progress

- baseline master-plan около `31%`
- live operational progress около `91%`
- baseline нельзя использовать как live status

### 2. Cross-account проблема была, но не является текущим главным блокером

Раньше:

- USER2 не мог честно управлять Chrome-процессом `pablito`

Сейчас:

- это уже не главный remaining blocker
- текущий главный blocker находится на стороне самого Chrome

### 3. Простое подтверждение доступа к Chrome не решает проблему

Это уже проверено.

Почему:

- после подтверждений ordinary attach всё равно не заработал
- helper-log показал Chrome-side policy message

### 4. Простое открытие `chrome://inspect` недостаточно

Это уже обсуждали и зафиксировали.

Причина:

- DevTools MCP сам не поднимает рабочий remote debugging surface

### 5. Ordinary Chrome и OpenClaw Debug browser не одно и то же

Ordinary Chrome:

- нужен для настоящего owner-profile path

Debug browser:

- отдельный isolated contour
- usable как fallback, если ordinary attach blocked

## Подтверждённая логическая цепочка по Chrome

1. Порт `9222` действительно слушает
2. HTTP discovery на `/json/version` даёт `404`
3. `DevToolsActivePort` существует и отдаёт browser websocket endpoint
4. websocket handshake на этом endpoint уходит в timeout
5. helper-log сообщает:
   `DevTools remote debugging requires a non-default data directory`
6. Следовательно, current Chrome policy блокирует default-profile remote debugging

## Что не надо делать в новом чате

- не надо снова предполагать, что нужно просто ещё раз нажать approve
- не надо снова лечить это только через cross-account switch
- не надо снова считать, что ordinary Chrome “почти attach-нулся” без подтверждённого action probe
- не надо переписывать handoff на основе памяти вместо свежих docs и runtime API

## Что стоит делать вместо этого

- опираться на live API и helper-log
- фиксировать truthful state в UI/docs
- либо принять default-profile attach как known issue текущего Chrome
- либо строить новый supported path через отдельный non-default `--user-data-dir`

## Практический next step

- если нужен быстрый operational browser path, использовать OpenClaw Debug browser
- если нужен именно ordinary owner profile, исследовать новый attach-path через non-default data dir
