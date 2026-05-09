# Wave 44-Y — Stealth browser + captcha solving

Расширяет `scripts/agent_tools/krab_browser.py` (Wave 44-T) тремя слоями:

1. **Stealth fingerprint masking** (`_browser_stealth.py`) — `playwright-stealth` patches:
   - `navigator.webdriver=false`, plugins/languages/timezone/codecs spoof
   - WebGL/Canvas fingerprint randomization
   - User-Agent rotation (recent macOS Chrome 130-132)
   - Headers: `Accept-Language`, `sec-ch-ua` matched к UA версии
2. **Captcha solving** (`_captcha_solver.py`) — 2captcha / CapSolver / anti-captcha:
   - reCAPTCHA v2/v3, hCaptcha, Cloudflare Turnstile, image captcha
   - Auto-detect on `open` (можно отключить `--skip-captcha`)
   - Explicit `solve_captcha` subcommand
   - Cost guard: `KRAB_CAPTCHA_MAX_SOLVES=5` per CLI invocation
   - Progress callback every 10s (parries Wave 44-W stagnation detector)
3. **Human-like behavior** (`_browser_humanize.py`):
   - `human_click` — bezier-curved mouse path, 8-15 steps, 10-30ms per step + 50-150ms dwell
   - `human_type` — per-char 50-180ms delays + occasional 200-500ms thinking pauses
   - `human_scroll` — multiple wheel events, random distances 30-100px

## Setup

```bash
# 1) Install deps
cd /Users/pablito/Antigravity_AGENTS/Краб
venv/bin/pip install "playwright-stealth>=2.0"

# 2) Get API key (one of):
#    https://2captcha.com/?from=...     ~$2.99 / 1000 reCAPTCHA v2 (~$0.003/solve)
#    https://capsolver.com              ~$0.80 / 1000 (~$0.0008/solve)
#    https://anti-captcha.com           ~$2 / 1000

# 3) Configure .env
KRAB_CAPTCHA_SERVICE=2captcha
KRAB_CAPTCHA_API_KEY=<your_key>
KRAB_CAPTCHA_MAX_SOLVES=5
KRAB_BROWSER_HUMANIZE_DEFAULT=1
KRAB_BROWSER_STEALTH_DEFAULT=1
```

## CLI

```bash
# Open URL — captcha auto-detected and solved if KRAB_CAPTCHA_SERVICE configured
venv/bin/python scripts/agent_tools/krab_browser.py open --url https://example.com

# Force-solve any captcha on the page
venv/bin/python scripts/agent_tools/krab_browser.py solve_captcha --url https://example.com

# Click human-like (default)
venv/bin/python scripts/agent_tools/krab_browser.py click --url ... --selector "#submit"

# Click instantly (debugging / speed)
venv/bin/python scripts/agent_tools/krab_browser.py click --url ... --selector "#submit" --no-humanize

# Disable stealth (debug raw Playwright behavior)
venv/bin/python scripts/agent_tools/krab_browser.py open --url ... --no-stealth
```

## ⚠️ ToS warning

Bypassing bot detection / solving captchas via 3rd-party API нарушает ToS многих сайтов
(Google, Cloudflare). Используйте только на ресурсах где у вас есть legitimate access
(своих аккаунтах, API endpoints у которых есть paid plan, etc).

User pavua явно осознаёт risk при включении KRAB_CAPTCHA_SERVICE != none.

## Cost estimates

| Service     | reCAPTCHA v2 | hCaptcha | Turnstile | Latency  |
|-------------|--------------|----------|-----------|----------|
| 2captcha    | ~$0.003      | ~$0.003  | ~$0.002   | 15-60s   |
| CapSolver   | ~$0.0008     | ~$0.0008 | ~$0.0005  | 5-30s    |
| anti-captcha| ~$0.002      | ~$0.002  | ~$0.001   | 15-60s   |

`KRAB_CAPTCHA_MAX_SOLVES=5` ограничивает spend за один CLI invocation: при reCAPTCHA v2
через 2captcha — максимум $0.015 / запрос. Counter сбрасывается между запусками процесса.

## Detection bypass effectiveness

| Defense                          | Bypass status  |
|----------------------------------|----------------|
| navigator.webdriver check        | ✓ playwright-stealth |
| Plugin/MIME-type fingerprinting  | ✓ playwright-stealth |
| Canvas/WebGL fingerprint         | ✓ playwright-stealth (randomized) |
| User-Agent / Client Hints        | ✓ rotation pool |
| Mouse movement entropy           | ✓ bezier paths |
| Typing rhythm analysis           | ✓ variable delays |
| reCAPTCHA v2/v3                  | ✓ 2captcha/capsolver |
| Cloudflare Turnstile             | ⚠ partial — works on simple challenges |
| Cloudflare full challenge (UAM)  | ✗ не bypassed (нужен residential proxy) |
| TLS fingerprint (JA3)            | ✗ uses Playwright's stock Chromium TLS |
| Behavioral analysis (long-term)  | ✗ heuristics на длинных sessions всё равно palят |

## Manual override flow

Если `KRAB_CAPTCHA_API_KEY` не настроен или solve_failed — получаем:

```json
{"ok": false, "type": "recaptcha_v2", "requires_manual": true, ...}
```

В этом случае Krab agent сообщает owner в DM:
"captcha detected on <url>, нужен manual override либо настроить KRAB_CAPTCHA_API_KEY".

Owner может:
1. Открыть URL в Chrome (CDP подключение → user уже залогинен), решить captcha вручную, передать управление обратно через `solve_captcha` (cookie из shared context подхватится автоматически).
2. Настроить API key и retry.

## Wave 44-W coordination

Captcha solving имеет latency 15-60s — может выглядеть как stagnation для detector
из Wave 44-W. `_captcha_solver.py` принимает `progress_cb` и эмитит uplate
"captcha solving (recaptcha_v2) 20s elapsed" каждые 10s, чтобы агент видел live activity.

При интеграции с Krab agent loop передавать progress_cb который пишет в transcript.
