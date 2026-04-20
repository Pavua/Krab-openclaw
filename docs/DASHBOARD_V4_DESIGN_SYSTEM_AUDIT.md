# Design System Audit: Krab Dashboard V4

**Date:** 2026-04-20  
**Pages audited:** 11 (index, chat, costs, inbox, swarm, translator, ops, settings, commands, research + prototype costs_v4_claude_design)  
**Source of truth:** `src/web/v4/liquid-glass.css`

---

## Summary

| Metric | Value |
|--------|-------|
| Pages audited | 11 |
| Canonical tokens in liquid-glass.css | 18 (12 core + 6 glass-layer) |
| Pages with inline token redefinitions | 6 |
| Pages missing `theme-toggle.js` | 1 (costs.html) |
| Stray hex colors not in palette | 9 distinct values |
| Component pattern drift instances | 4 major |
| Nav consistency violations | 3 |
| Mobile tab bar missing | 1 (commands.html) |
| Font-family drift | 3 variants in use |

Top-3 severity findings:
1. **inbox.html** — три стрэй цвета `#ff4757`, `#ffa502`, `#1e90ff` (не входят в палитру) и hardcoded `background-color: #0f0f13` вместо `var(--bg-primary)`
2. **chat.html** — использует несуществующие переменные `--bg-color`, `--text-main`, `--accent-color` (iOS-era legacy fallbacks с `#007aff`) вместо канонических `--bg-primary`, `--accent-cyan`
3. **index.html** — переопределяет `.status-dot` с нестандартными цветами `#10b981` / `#ef4444` вместо `var(--accent-green)` / `var(--accent-red)`, а также ссылается на `--spacing-md` / `--spacing-sm` — переменные не определённые в liquid-glass.css

---

## 1. Tokens Inventory

Канонические CSS-переменные из `liquid-glass.css` (`:root` dark + `html[data-theme="light"]`):

| Token | Dark value | Light value | Category |
|-------|-----------|-------------|----------|
| `--glass-bg` | `rgba(255,255,255,0.06)` | `rgba(255,255,255,0.6)` | Glass layer |
| `--glass-bg-hover` | `rgba(255,255,255,0.10)` | `rgba(255,255,255,0.8)` | Glass layer |
| `--glass-border` | `rgba(255,255,255,0.10)` | `rgba(0,0,0,0.08)` | Glass layer |
| `--glass-blur` | `40px` | — | Glass layer |
| `--glass-specular` | `inset 0 0.5px 0 rgba(...)` | — | Glass layer |
| `--glass-shadow` | `0 8px 40px rgba(0,0,0,0.25)` | — | Glass layer |
| `--bg-primary` | `#0a0a1a` | `#f5f7fb` | Background |
| `--bg-surface` | `#111118` | `#ffffff` | Background |
| `--accent-cyan` | `#7dd3fc` | `#0284c7` | Accent |
| `--accent-purple` | `#a78bfa` | `#7c3aed` | Accent |
| `--accent-green` | `#34d399` | `#059669` | Accent |
| `--accent-red` | `#f87171` | `#dc2626` | Accent |
| `--accent-yellow` | `#fbbf24` | `#d97706` | Accent |
| `--radius-card` | `20px` | — | Radius |
| `--radius-button` | `12px` | — | Radius |
| `--radius-input` | `14px` | — | Radius |
| `--font-primary` | `-apple-system, BlinkMacSystemFont, 'Outfit', sans-serif` | — | Typography |

**Заметно отсутствующие:** `--spacing-*`, `--text-*` (muted, main), `--bg-color`, `--border-color`, `--accent-color` — всё это придумывается индивидуально каждой страницей.

### Использование токенов по страницам

| Page | `--bg-primary` | `--accent-*` | `--glass-*` | `--radius-*` | Hardcoded bg hex | Hardcoded accent hex |
|------|---------------|-------------|-------------|-------------|-----------------|---------------------|
| index.html | Нет (`#0b0f19`) | Частично | Да | Частично | `#0b0f19`, `#0f172a` | `#10b981`, `#ef4444`, `#fbbf24` |
| chat.html | Нет (uses `--bg-color`) | Нет (uses `--accent-color`) | Да (с fallback) | Нет | — | `#007aff` |
| costs.html | Да | Нет (hardcoded hex) | Да | Да | — | `#bae6fd`, `#6ee7b7`, `#fca5a5`, `#c4b5fd` |
| inbox.html | Нет (`#0f0f13`) | Нет | Частично | Нет | `#0f0f13` | `#ff4757`, `#ffa502`, `#1e90ff` |
| swarm.html | Нет | Да | Да | Да | — | — |
| translator.html | Нет | Да (с fallback) | Да (с fallback) | Нет | — | `#22c55e`, `#eab308`, `#9ca3af` |
| ops.html | Нет | Да (fallback) | Да (fallback) | Нет | — | — |
| settings.html | Нет | Частично | Да (fallback) | Нет | — | `#7dd3fc` (raw) |
| commands.html | Нет (`#0b0f19`) | Частично | Да | Нет | `#0b0f19` | — |
| research.html | Нет (`#0f0f13`) | Нет | Нет | Нет | `#0f0f13` | `#38bdf8`, `#ef4444` |
| prototype costs_v4 | Да | Нет (hardcoded) | Да | Да | — | `#6ee7b7`, `#fca5a5`, `#c4b5fd`, `#bae6fd` |

**Общий вывод:** ни одна страница не использует токены на 100%. Наиболее compliant — swarm.html и ops.html. Наиболее далёкие — inbox.html и research.html.

---

## 2. Component Pattern Drift

| Component | Canonical definition | Drift pages | Drift pattern | Recommendation |
|-----------|---------------------|------------|----------------|----------------|
| `.glass-button` | `liquid-glass.css` L208 | ops.html, settings.html | Переопределены в `<style>` с теми же значениями | Убрать дублирование, использовать shared |
| `.glass-input` | `liquid-glass.css` L249 | chat.html, costs.html | chat — полная замена с `background: rgba(0,0,0,0.2)` (без blur); costs — переопределение без backdrop | Унифицировать или выделить `.glass-input-minimal` |
| `.badge` | `liquid-glass.css` L437 | inbox.html, costs.html | inbox — полное переопределение (другая геометрия, цвета); costs — переопределяет без `position:absolute` | Разделить `.badge` (notification dot) и `.badge-pill` (label) |
| `.skeleton` | `liquid-glass.css` L512 | index.html, costs.html | index — другой keyframe (`loading` вместо `glassShimmer`), другие opacities; costs — другой keyframe (`shimmer`) | Единый shimmer animation в shared CSS |
| `.status-dot` | `liquid-glass.css` L413 | index.html | Полное переопределение с `#10b981`/`#ef4444` вместо `var(--accent-green/red)` | Удалить inline, использовать canonical |
| `.pill-*` | `liquid-glass.css` L453 | ops.html, settings.html, research.html | ops/settings — полные дубликаты; research — использует `#38bdf8` для `pill-info` вместо `--accent-cyan` | Только `@import`, никаких копий |
| `.glass-pill` (chat) | Не определён в shared | chat.html только | Нигде кроме chat | Рассмотреть добавление в shared как `.mode-pill` |

---

## 3. Nav Consistency Matrix

Канонический nav-порядок из index.html:
`Hub / Chat / Costs / Inbox / Swarm / Translator / Ops / Research / Settings / Commands`

| Page | Nav present | Active link | Research link | Bell wrapper | Mobile tab bar | Commands в tab bar |
|------|-----------|-------------|--------------|-------------|----------------|-------------------|
| index.html | Да | Hub | Да | Да (полный) | Да | Нет |
| chat.html | Да | Chat | Да | Да (без aria-label) | Да | Нет |
| costs.html | Да | Costs | Да | Да (без aria-label) | Да | Нет |
| inbox.html | Да | Inbox | Да | Да | Да | Нет |
| swarm.html | Да | Swarm | Да | Да | Да | Нет |
| translator.html | Да | Translator | Да | Да | Да | Нет |
| ops.html | Да | Ops | Да | Да | Да | Нет |
| settings.html | Да | Settings | Да | Да | Да | Нет |
| commands.html | Да | Commands | Да | Да | **Нет** | — |
| research.html | Да | Research | Да (active) | Нет | Да (переопределён) | Нет |
| prototype costs_v4 | Да | Costs | **Нет** | Да | Нет | Нет |

**Нарушения:**

1. **commands.html** — нет mobile `glass-tab-bar`. Единственная страница без него.
2. **prototype costs_v4** — нет `Research` в nav-links (пропущен при генерации).
3. **research.html** — переопределяет `.glass-tab-bar` прямо в `<style>` (override shared с `position: fixed; ... display: flex`), делает его `<nav>` вместо `<div>`.
4. **chat.html** — bell `<a>` не имеет `aria-label` (в index.html есть `aria-label="Notifications (0 new)"`). Accessibility inconsistency.

**Mobile tab bar items** (неполная консистентность):
- index: Hub / Chat / Costs / Swarm / Trans (5 items)
- chat: Hub / Chat / Costs / Swarm / Trans (5 items)
- costs: Hub / Chat / Costs / Inbox / Swarm / Trans (6 items — Inbox добавлен)
- inbox: Hub / Costs / Inbox / Swarm / Trans (5 items — Chat отсутствует)
- ops: Hub / Chat / Costs / Inbox / Ops / Trans (6 items — Ops добавлен, разный порядок)
- translator: Hub / Chat / Costs / Swarm / Trans (5 items)
- settings: Hub / Chat / Swarm / Trans / Settings (5 items — порядок другой)
- swarm: Hub / Chat / Costs / Swarm / Trans (5 items)

Нет ни одной пары страниц с идентичным составом tab bar.

---

## 4. Typography Drift

Canonical typography из liquid-glass.css:

| Class | rem value | Defined in shared |
|-------|----------|------------------|
| `.text-xs` | `0.75rem` | Да |
| `.text-sm` | `0.875rem` | Да |
| (h3) | `1rem` | Да (h3 rule) |
| `.text-lg` | `1.125rem` | Да |
| (h2) | `1.25rem` | Да (h2 rule) |
| (h1) | `1.75rem` | Да (h1 rule) |
| `.text-xl` | Не определён | — |
| `.text-2xl` | Не определён | — |
| `.text-3xl` | Не определён | — |

`.text-xl`, `.text-2xl`, `.text-3xl` определены в costs.html, prototype costs_v4 — но не в shared. В index.html определены локально `.text-xl` и `.text-2xl`. Нет единой шкалы для этих классов.

| Аномалия | Страница |
|----------|---------|
| `font-size: 0.85rem` / `0.8rem` / `0.9rem` — вне шкалы | chat.html, ops.html, translator.html |
| `font-size: 14px` (px вместо rem) | index.html (toast, spinner) |
| `font-size: 16px` | commands.html (iOS zoom prevention) |
| `font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto...` | research.html — полностью другой стек |
| `font-family: system-ui, -apple-system, sans-serif` (без Outfit) | chat.html, costs.html, inbox.html, ops.html |
| `font-family: 'Outfit', system-ui...` (правильный) | index.html, commands.html, translator.html |

research.html использует полностью отличный font-stack без Outfit — вероятно, был создан отдельно.

---

## 5. Color Drift (Stray Values)

Цвета присутствующие в HTML-файлах, которых нет в liquid-glass.css:

| Found value | Intended canonical | Pages | Severity |
|------------|-------------------|-------|----------|
| `#ff4757` | `var(--accent-red)` = `#f87171` | inbox.html | High — другой красный |
| `#ffa502` | `var(--accent-yellow)` = `#fbbf24` | inbox.html | High — другой жёлтый |
| `#1e90ff` | `var(--accent-cyan)` = `#7dd3fc` | inbox.html | High — другой синий (Tailwind-era) |
| `#007aff` | `var(--accent-cyan)` | chat.html | High — iOS blue вместо sky-cyan |
| `#10b981` | `var(--accent-green)` = `#34d399` | index.html | Medium — Tailwind green-500 vs Emerald-400 |
| `#ef4444` | `var(--accent-red)` = `#f87171` | index.html, research.html | Medium — Tailwind red-500 |
| `#38bdf8` | `var(--accent-cyan)` | research.html | Medium — Sky-400 vs sky-300 |
| `#22c55e` | `var(--accent-green)` | translator.html | Low — fallback value в var() |
| `#eab308` | `var(--accent-yellow)` | translator.html | Low — fallback value в var() |
| `#9ca3af` | `var(--text-muted)` | translator.html | Low — fallback value в var() |
| `#0b0f19` | `var(--bg-primary)` = `#0a0a1a` | index.html, commands.html | Medium — почти совпадает, но не токен |
| `#0f0f13` | `var(--bg-primary)` | inbox.html, research.html | Medium — темнее canonical |
| `#0f172a` | `var(--bg-surface)` = `#111118` | index.html (modal bg) | Low — Tailwind slate-900 |

**Самый критичный случай:** inbox.html использует три Tailwind-era / iOS-era цвета (`#ff4757`, `#ffa502`, `#1e90ff`) которые создают визуальный разрыв с остальным дашбордом, особенно потому что это цвета severity-badge — семантически важных элементов.

---

## 6. Spacing Scale

Spacing utilities определены в `liquid-glass.css`:
```
.mt-1/.mt-2/.mt-3/.mt-4   (0.25 / 0.5 / 1 / 1.5rem)
.mb-1/.mb-2/.mb-3/.mb-4   (то же)
.gap-1/.gap-2/.gap-3/.gap-4 (то же)
```

**Drift:**

| Аномалия | Страница |
|----------|---------|
| `--spacing-md`, `--spacing-sm` используются но не определены в shared | index.html |
| `padding: 0.75rem` / `1.25rem` / `2rem` хардкодом (нет utility) | все страницы |
| costs.html переопределяет `.gap-*`, `.mt-*`, `.mb-*` локально | costs.html |
| `.p-2 { padding: 0.5rem }`, `.p-4 { padding: 1rem }` — не в shared | costs.html, index.html (разные значения) |

`p-2` в index.html = `var(--spacing-sm)` (undefined), в costs.html = `0.5rem`. Функционально одинаково, но разные source.

---

## 7. Naming Drift (CSS Class Names)

| Canonical name | Drift variants | Pages | Type |
|----------------|---------------|-------|------|
| `.glass-button` | нет drift в имени, но переопределения | ops, settings | Override drift |
| `.badge` | `.badge-pill` (prototype), `.meta-badge` (commands) | costs_v4, commands | Semantic split |
| `.pill-*` | `.pill` + `.pill-ok`/`.pill-off` (settings), `.pill-info`/`.pill-warning`/`.pill-done` разные (research) | settings, ops, research | Value drift |
| `.glass-input` | `.search-input` (commands), `.budget-input` (settings), `.kbd-search` (chat) | commands, settings, chat | Specialization без базового |
| `.text-muted` | `--text-muted` переменная (inbox, research определяют локально), `rgba(...)` инлайн | inbox, research | Mixed approach |
| `.glass-pill` (chat mode toggle) | нет аналога в shared | chat | Orphan component |
| `.stat-card` | разные layout в каждой странице | costs, inbox, swarm, ops | Semantic same, CSS different |
| `.filter-pill` (swarm, commands) | нигде не определён в shared | swarm, commands | Orphan |

---

## 8. Priority Cleanups

В порядке убывания важности:

1. **[High] Исправить inbox.html stray colors** — заменить `#ff4757`→`var(--accent-red)`, `#ffa502`→`var(--accent-yellow)`, `#1e90ff`→`var(--accent-cyan)` во всех трёх badge-вариантах и inline styles. Это severity-badges — визуальный сигнал, потому несоответствие системе максимально заметно.

2. **[High] Починить chat.html ghost variables** — `--bg-color`, `--text-main`, `--accent-color` не существуют в liquid-glass.css. Fallback `#007aff` создаёт iOS-синий для bubble вместо sky-cyan. Заменить на `--bg-primary`, `--accent-cyan`.

3. **[High] Вынести `.skeleton` в shared** — три разных keyframe (`glassShimmer`, `shimmer`, `loading`) с разными opacity-кривыми. Выбрать один (canonical `glassShimmer`), удалить локальные.

4. **[Medium] Вынести `.text-xl`/`.text-2xl`/`.text-3xl` в liquid-glass.css** — эти классы используются в 5+ страницах, но не определены в shared. Добавить в Typography section (`1.25rem`, `1.5rem`, `1.875rem`).

5. **[Medium] Унифицировать mobile tab bar состав** — определить canonical 5-item состав (Hub / Chat / Costs / Swarm / Translator) и применить везде. Добавить tab bar в commands.html.

6. **[Medium] index.html: заменить `#10b981`/`#ef4444` в .status-dot** — использовать `var(--accent-green)` и `var(--accent-red)`. Также убрать `--spacing-md`/`--spacing-sm` или добавить их в :root.

7. **[Medium] Добавить `theme-toggle.js` в costs.html** — единственная production-страница без него. Dark/light toggle не работает на Costs.

8. **[Medium] research.html** — полностью переработан без использования shared tokens: другой font-stack, хардкодные `#0f0f13`, `#38bdf8`, `#ef4444`, переопределённый `.glass-tab-bar`. Требует приведения к стандарту.

9. **[Low] Добавить `.p-2`/`.p-4`/`.p-3` spacing utilities в liquid-glass.css** — используются во всех страницах через локальные копии.

10. **[Low] Семантически разделить `.badge`** — canonical badge в shared — это notification dot (`position: absolute`, маленький). Страницы используют его как label-pill. Добавить отдельный `.badge-label` или `.label-pill` в shared.

11. **[Low] Prototype costs_v4 nav** — добавить `Research` ссылку в nav-links для полного parity с production pages.

12. **[Low] aria-label на bell** — chat.html, costs.html, swarm.html и другие не имеют `aria-label` на `.nav-bell`, в отличие от index.html. Добавить для accessibility consistency.

---

## Приложение: Быстрая таблица compliance

| Page | Token use | No stray colors | Tab bar | Font correct | Bell aria | Score |
|------|-----------|-----------------|---------|-------------|-----------|-------|
| index.html | Частично | Нет | Да | Да | Да | 3/5 |
| chat.html | Нет | Нет | Да | Нет | Нет | 1/5 |
| costs.html | Частично | Нет | Да | Нет | Нет | 1/5 |
| inbox.html | Нет | **Нет (3 stray)** | Да | Нет | Нет | 0/5 |
| swarm.html | Да | Да | Да | N/A | Да | 4/5 |
| translator.html | Да | Да (fallbacks OK) | Да | Частично | N/A | 3/5 |
| ops.html | Да | Да | Да | N/A | Да | 4/5 |
| settings.html | Частично | Нет | Да | N/A | Да | 3/5 |
| commands.html | Частично | Да | **Нет** | Да | Да | 3/5 |
| research.html | Нет | Нет | Да (custom) | **Нет** | Нет | 0/5 |
| prototype costs_v4 | Частично | Нет | Нет | Нет | Нет | 0/5 |
