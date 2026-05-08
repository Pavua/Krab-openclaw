# Krab Ear Agent — Codesign Fix Required

## Проблема

3 crashes в `~/Library/Logs/DiagnosticReports/KrabEarAgent-2026-05-08-*.ips`:
- 00:48, 01:15, 01:31 (May 8)
- Все идентичные: `SIGKILL (Code Signature Invalid)` → `EXC_BAD_ACCESS UNKNOWN_0x32 at 0x100708000`
- Crashed Thread = Thread 0, frames только из `dyld` bootstrap (никакой Swift код не выполнился)
- Process умирает **до** Swift `main()` — dyld не может валидировать подпись `__TEXT` страниц

## Root cause

`codesign -dvv "/Applications/Krab Ear.app"` показывает:
```
flags=0x20002(adhoc, linker-signed)
Sealed Resources=none
TeamIdentifier=not set
```

Bundle структура нарушена: **`_CodeSignature/CodeResources` отсутствует** или повреждён. `spctl --assess` → "code has no resources but signature indicates they must be present".

## Timing

Binary mtime/ctime = **2026-05-08 01:28:29** (между 2-м и 3-м crash'ем). Кто-то пере-собрал/перезаписал бинарь во время серии падений. Первые 2 (00:48, 01:15) — старая копия с broken signature, третий (01:31) — новая копия и **тоже broken**.

Скорее всего связано с прошлой сессией где были fix'ы для `SingleInstanceGuard.swift` (Pipe deadlock) — пере-собрали Mach-O, но не пере-подписали bundle целиком.

## Fix (выполнить вручную)

```bash
# 1) Очистить quarantine (на всякий)
xattr -cr "/Applications/Krab Ear.app"

# 2) Пере-подписать ВЕСЬ bundle (рекурсивно, ad-hoc)
codesign --force --deep --sign - "/Applications/Krab Ear.app"

# 3) Verify
codesign -dvv "/Applications/Krab Ear.app"
spctl --assess -vv "/Applications/Krab Ear.app"

# 4) Запустить
open "/Applications/Krab Ear.app"
```

После этого `Sealed Resources=` должно показать non-zero count, `_CodeSignature/CodeResources` должен существовать.

## Permanent fix в Xcode build

В Xcode build settings:
- Code Signing Identity: `Sign to Run Locally` (или Developer ID Application)
- Code Signing Style: Automatic
- В build phase «Sign Frameworks» убедиться что bundle resources подписываются

После каждого rebuild проверять `_CodeSignature/CodeResources` присутствует.

## Сейчас status

KE app не крашится постоянно (нет новых ips после 01:31), но если откроешь и снова получишь SIGKILL — выполни 4 команды выше.
