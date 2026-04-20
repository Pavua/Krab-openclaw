# WCAG 2.1 AA Accessibility Audit — Costs Dashboard V4

**File audited:** `src/web/prototypes/costs_v4_claude_design.html`
**CSS audited:** `src/web/v4/liquid-glass.css`
**Standard:** WCAG 2.1 Level AA
**Date:** 2026-04-20
**Auditor:** Krab Code Worker (automated structural + contrast analysis)

---

## Executive Summary

| Severity | Count |
|----------|-------|
| Critical | 4 |
| Major | 7 |
| Minor | 5 |
| **Total** | **16** |

**Color contrast (dark theme):** All tested foreground/background pairs pass WCAG AA 4.5:1 for normal text. No contrast failures found in dark mode.

**Top issues:** Missing canvas accessibility (`<canvas>` has no ARIA), no keyboard focus trap in the budget modal, missing `role="dialog"` and `aria-modal` on modal overlay, live countdown has no `aria-live`, and multiple buttons with symbol labels ("×") lack `aria-label`.

---

## Findings

### Critical Findings

| ID | Location | Issue | WCAG Criterion |
|----|----------|-------|----------------|
| C1 | `<canvas id="donut-chart">` | No `role="img"`, no `aria-label`. Chart.js donut is invisible to screen readers — 100% of provider cost data is inaccessible. | 1.1.1 Non-text Content |
| C2 | `<canvas id="trend-chart">` | Same as C1. Trend line chart has no accessible name or description. | 1.1.1 Non-text Content |
| C3 | `<div class="modal-overlay" id="budget-modal">` | Missing `role="dialog"`, `aria-modal="true"`, `aria-labelledby`. No focus trap: Tab can escape the modal into background content. Focus returns to the page on close instead of the trigger button. | 4.1.3 Status Messages; 2.1.2 No Keyboard Trap (inverse: trap IS needed) |
| C4 | `<html lang="en">` | Page `lang` is set to `"en"` but most user-facing strings are Russian (`"Нет данных"`, `"Загрузка..."`, `"Бюджет не установлен"`, toast messages, banner text, history info label). Screen readers will use the wrong voice/pronunciation engine for Russian content. | 3.1.1 Language of Page |

### Major Findings

| ID | Location | Issue | WCAG Criterion |
|----|----------|-------|----------------|
| M1 | `<span id="nav-timer">↻ 30s</span>` | Live countdown updates every second via `setText()` but has no `aria-live` attribute. Screen reader users hear nothing, or if a live region is synthesized by SR, it announces every second (disruptive). Should be `aria-live="off"` (suppress) with a separate `aria-live="polite"` region that announces only on data refresh. | 4.1.3 Status Messages |
| M2 | `<span class="status-dot ok" id="api-status">` | API status dot is purely visual (color + pulse animation). No text alternative. Screen readers cannot determine API state. Needs `role="status"` and a visually-hidden text child updated on state change (`"API connected"` / `"API offline"`). | 1.3.1 Info and Relationships; 1.4.1 Use of Color |
| M3 | `<button class="banner-close" id="runway-banner-close" title="Закрыть">×</button>` | Button label is the Unicode character "×" (U+00D7). `title` attribute is not reliably announced by all screen readers (especially on mobile). Needs `aria-label="Закрыть"`. | 4.1.2 Name, Role, Value |
| M4 | `<span class="filter-badge-x" id="clear-provider-filter">×</span>` | This interactive element is a `<span>` with a click listener. It is not a `<button>`, so it is not focusable by keyboard and has no accessible role. Tab cannot reach it. | 2.1.1 Keyboard; 4.1.2 Name, Role, Value |
| M5 | `<div class="trend-toggle" id="trend-toggle">` containing `<button>` elements | Trend toggle (Cost / Calls / Tokens) is a group of mutually exclusive buttons acting as radio buttons. There is no `role="group"` or `role="radiogroup"` with `aria-label`. Arrow key navigation between options (expected UX for radio groups) is not implemented — only Tab/click works. | 1.3.1 Info and Relationships |
| M6 | `showToast()` function | Toast elements are appended dynamically to `#toast-container` but the container has no `aria-live` region attribute. Toasts (including error/success feedback for budget save, export, API failure) are invisible to screen readers. Needs `aria-live="polite"` (or `"assertive"` for errors) on `#toast-container`. | 4.1.3 Status Messages |
| M7 | `<div class="runway-alert-banner" id="runway-banner">` | Alert banner (shown on low runway) has no `role="alert"`. When it becomes visible via class toggle, screen readers are not notified. Also has no `aria-atomic="true"`. | 4.1.3 Status Messages |

### Minor Findings

| ID | Location | Issue | WCAG Criterion |
|----|----------|-------|----------------|
| N1 | All `<table>` elements (`#model-tbody`, `#history-tbody`) | Tables have no `<caption>` and no `aria-label`. Screen readers announce "table" without context on what data it contains. | 1.3.1 Info and Relationships |
| N2 | `<a href="/v4/inbox" class="nav-bell" title="Notifications">` | Bell link has `title="Notifications"` but no `aria-label`. `title` is not reliably surfaced as accessible name on all platforms. Should be `aria-label="Уведомления"`. The bell count `<span id="nav-bell-count">` inside lacks `aria-live` — count updates are silent. | 4.1.2 Name, Role, Value |
| N3 | Skeleton loader elements (`.skeleton` divs/spans) | Loading placeholders have no `aria-busy="true"` on their parent containers, and no `aria-label="Загрузка..."`. Screen readers may announce empty or "0.00" skeleton content before data loads. | 4.1.3 Status Messages |
| N4 | `input[type="date"]` elements (`#date-from`, `#date-to`) | Labels "From" / "To" are `<label>` elements but are **not** associated with inputs via `for`/`id` or wrapping. Labels are `<label class="text-muted text-xs">From</label>` without `for="date-from"`. Programmatic association is missing. | 1.3.1 Info and Relationships; 4.1.2 Name, Role, Value |
| N5 | `css: .glass-input { outline: none; }` | The `outline:none` declaration removes the default browser focus indicator on the budget input field. The replacement focus style (`box-shadow: 0 0 0 3px rgba(125,211,252,0.25)`) is semi-transparent and may be insufficient in some high-contrast environments. `:focus-visible` with opaque outline is preferred. | 2.4.7 Focus Visible |

---

## Color Contrast Audit (Dark Theme)

Background: `#0a0a1a` (L = 0.003562)

| Text Color | Hex / Effective | Contrast Ratio | Normal (4.5:1) | Large (3:1) |
|------------|-----------------|----------------|----------------|-------------|
| Body text | `#ffffff` | 19.60:1 | PASS | PASS |
| `.text-muted` (rgba 255,255,255,0.72) | `#bababe` (blended) | 10.13:1 | PASS | PASS |
| `.text-accent` `#bae6fd` | `#bae6fd` | 14.77:1 | PASS | PASS |
| `--accent-cyan` `#7dd3fc` | `#7dd3fc` | 11.76:1 | PASS | PASS |
| `.text-success` `#6ee7b7` | `#6ee7b7` | 12.86:1 | PASS | PASS |
| `--accent-green` `#34d399` | `#34d399` | 10.20:1 | PASS | PASS |
| `.text-error` `#fca5a5` | `#fca5a5` | 10.33:1 | PASS | PASS |
| `--accent-red` `#f87171` | `#f87171` | 7.09:1 | PASS | PASS |
| `.text-warning` `#fde68a` | `#fde68a` | 15.74:1 | PASS | PASS |
| `--accent-yellow` `#fbbf24` | `#fbbf24` | 11.74:1 | PASS | PASS |
| `.text-purple` / `.text-accent-purple` `#c4b5fd` | `#c4b5fd` | 10.62:1 | PASS | PASS |
| `--accent-purple` `#a78bfa` | `#a78bfa` | 7.20:1 | PASS | PASS |
| `<th>` color `#bae6fd` | `#bae6fd` | 14.77:1 | PASS | PASS |
| Nav link inactive (rgba 255,255,255,0.6) | `#9d9da3` (blended) | 7.27:1 | PASS | PASS |
| Tab bar item inactive (rgba 255,255,255,0.5) | `#84848c` (blended) | 5.28:1 | PASS | PASS |
| Bell badge: `#0a0a1a` on `#f87171` | — | 7.09:1 | PASS | PASS |
| White text on glass-card (rgba 255,255,255,0.06 overlay) | effective `#181827` | 17.52:1 | PASS | PASS |
| Progress fill `#34d399` vs track (rgba 0,0,0,0.3 overlay) | fill vs `#070712` | 10.42:1 | PASS (graphical 3:1) | PASS |

**Result: 0 color contrast failures in dark theme.**

> Note: Light theme (`[data-theme="light"]`) introduces different color values. Light theme was not fully audited in this pass. The light theme reset of `--accent-cyan` to `#0284c7` and `--accent-green` to `#059669` on `#f5f7fb` should be verified separately (expected to pass).

---

## Keyboard Navigation Audit

| Element | Reachable via Tab | Expected Behavior | Finding |
|---------|-------------------|-------------------|---------|
| Nav links (`<a>`) | Yes | Tab through, Enter activates | OK |
| Export CSV / Export JSON (`<button>`) | Yes | Enter/Space activates | OK |
| Set Budget button (`<button>`) | Yes | Opens modal | OK — but modal has no focus trap |
| Budget modal — input focus on open | Yes | Focus jumps to `#budget-input` via `openModal()` | OK |
| Budget modal — Cancel / Save buttons | Yes | Both reachable inside modal | OK |
| Budget modal — Escape to close | **No** | No `keydown` Escape handler on modal | FAIL |
| Budget modal — focus trap | **No** | Tab escapes to background page content | FAIL (C3) |
| Budget modal — focus return on close | **No** | Focus not returned to trigger on `closeModal()` | FAIL (C3) |
| Banner close `×` button | Yes | Closes banner | OK reachable, but label fail (M3) |
| Clear provider filter `×` | **No** | `<span>` not focusable | FAIL (M4) |
| Prev / Next pagination buttons | Yes | Navigates pages | OK |
| Date filter inputs | Yes | Tab navigates, change event fires | OK — but label association missing (N4) |
| Reset date button | Yes | Clears filters | OK |
| Trend toggle buttons (Cost/Calls/Tokens) | Yes | Click switches metric | OK — but no arrow-key support (M5) |
| Donut legend items (`.legend-item`) | **No** | `<div>` with click, not focusable | FAIL (related to M4 pattern) |
| Bell dropdown toggle | Yes | Click opens/closes, Escape closes | Partial OK — keyboard open only via Enter on link |
| Mobile tab bar links | Yes | Standard `<a>` links | OK |

---

## Screen Reader Compatibility

| Element | SR Behavior (expected) | Finding |
|---------|------------------------|---------|
| `<canvas id="donut-chart">` | Read as empty or skipped | FAIL — all cost data inaccessible (C1) |
| `<canvas id="trend-chart">` | Read as empty or skipped | FAIL — trend data inaccessible (C2) |
| Toast notifications | Silent | FAIL — no `aria-live` on container (M6) |
| Runway alert banner | Silent when shown | FAIL — no `role="alert"` (M7) |
| API status dot | Cannot determine state | FAIL — color-only indicator (M2) |
| Live countdown `↻ 30s` | Announces every second (noisy) or silent | FAIL — no `aria-live` strategy (M1) |
| Modal `#budget-modal` | Not announced as dialog | FAIL — no `role="dialog"` (C3) |
| `×` close buttons | Read as "multiplication sign" or "times" | FAIL — needs `aria-label` (M3) |
| Date inputs `#date-from`, `#date-to` | No label read (labels not associated) | FAIL (N4) |
| Skeleton loaders | May read "0.00" or empty string | Partial FAIL (N3) |
| Page language | Russian strings read in English voice | FAIL — `lang="en"` mismatch (C4) |
| `<h1>`, `<h2>`, `<h3>` hierarchy | Logical heading structure present | OK |
| Tables without caption | "Table" with no description | FAIL (N1) |
| Bell count `<span>` updates | Silent count change | FAIL (N2) |
| SVG icons in nav/tab bar | No `aria-label`, no `aria-hidden` | Minor — decorative SVGs adjacent to text labels, acceptable |

---

## Touch Target Audit

Minimum required: 44×44 CSS px (WCAG 2.5.5 AAA; WCAG 2.5.8 AA in 2.2)

| Element | Estimated Size | Pass / Fail |
|---------|----------------|-------------|
| Export CSV / JSON buttons | ~32px height × ~80px width | FAIL — height ~32px (padding `.35rem .9rem` + font) |
| Set Budget button | ~38px height × ~100px | Marginal — close to 44px but border-only glass style |
| Prev / Next pagination buttons | ~32px height (same `.export-btn` style) | FAIL — height ~32px |
| Reset date button | ~32px height | FAIL |
| Banner close `×` | `font-size:1.1rem; padding:0` — approx 20×20px | FAIL — critically small |
| Bell icon link | `padding:0.5rem` around 20×20 SVG → ~36×36px | FAIL — below 44px |
| Trend toggle buttons | `padding:.3rem .85rem` → approx 28px height | FAIL |
| Legend items `.legend-item` | `padding:.3rem .5rem` → ~28px height | FAIL |
| Mobile tab bar items | 72px bar height, items ~56px tall | PASS |
| Nav links | `height:100%` = 56px nav | PASS |
| Budget modal Cancel / Save | `padding:.6rem 1.25rem` → ~38px | Marginal |

---

## Priority Fixes (Ordered by Impact)

### Priority 1 — Canvas Charts: Add Accessible Data Tables

```html
<!-- Provider Donut -->
<canvas id="donut-chart" role="img" aria-label="Распределение расходов по провайдерам"></canvas>
<!-- After chart render, add a visually-hidden data table -->
<table class="sr-only" id="donut-data-table" aria-label="Данные диаграммы провайдеров">
  <caption>Расходы по провайдерам</caption>
  <!-- Populated by JS alongside chart render -->
</table>

<!-- Trend Chart -->
<canvas id="trend-chart" role="img" aria-label="Тренд расходов по дням"></canvas>
```

Add `.sr-only` CSS:
```css
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0,0,0,0);
  white-space: nowrap;
  border: 0;
}
```

### Priority 2 — Budget Modal: Focus Trap + ARIA Dialog

```html
<div class="modal-overlay" id="budget-modal"
     role="dialog"
     aria-modal="true"
     aria-labelledby="modal-title">
  <div class="glass-card modal-content">
    <h3 class="text-xl mb-4 text-accent" id="modal-title">Set Monthly Budget</h3>
    ...
  </div>
</div>
```

JS additions:
```js
function openModal() {
  const m = document.getElementById('budget-modal');
  if (m) {
    m._triggerEl = document.activeElement;  // remember trigger
    m.classList.add('active');
    document.getElementById('budget-input').focus();
    // Escape listener
    m._escHandler = (e) => { if (e.key === 'Escape') closeModal(); };
    document.addEventListener('keydown', m._escHandler);
    // Focus trap (Tab cycle within modal)
    m._trapHandler = trapFocusHandler(m);
    m.addEventListener('keydown', m._trapHandler);
  }
}
function closeModal() {
  const m = document.getElementById('budget-modal');
  if (m) {
    m.classList.remove('active');
    document.removeEventListener('keydown', m._escHandler);
    m.removeEventListener('keydown', m._trapHandler);
    if (m._triggerEl) m._triggerEl.focus();  // return focus
  }
}
```

### Priority 3 — Fix `lang` Attribute and Add `aria-live` Regions

```html
<!-- Set primary language to Russian -->
<html lang="ru">
```

Add live regions:
```html
<!-- Toast container: announce to SR -->
<div class="toast-container" id="toast-container"
     aria-live="polite"
     aria-atomic="false"></div>

<!-- Runway banner: announce on visibility change -->
<div class="runway-alert-banner" id="runway-banner" role="alert" aria-atomic="true">

<!-- API status with SR text -->
<span class="status-dot ok" id="api-status" role="status" aria-label="API подключён">
  <span class="sr-only" id="api-status-text">API подключён</span>
</span>

<!-- Countdown: suppress from SR -->
<span class="text-muted text-sm" id="nav-timer" aria-live="off" aria-hidden="true">↻ 30s</span>
```

### Priority 4 — Fix Interactive `<span>` Elements

Replace `<span>` click targets with `<button>`:

```html
<!-- Clear provider filter -->
<button type="button" class="filter-badge-x" id="clear-provider-filter"
        aria-label="Сбросить фильтр провайдера">×</button>

<!-- Banner close — add aria-label (already a <button>) -->
<button class="banner-close" id="runway-banner-close" aria-label="Закрыть предупреждение">×</button>
```

Legend items — make keyboard accessible:
```html
<div class="legend-item" role="button" tabindex="0" data-provider="...">
```
Or better: use `<button>` with `.legend-item` class.

### Priority 5 — Associate Date Labels, Add Table Captions

```html
<label class="text-muted text-xs" for="date-from">From</label>
<input type="date" id="date-from">
<label class="text-muted text-xs" for="date-to">To</label>
<input type="date" id="date-to">
```

Tables:
```html
<table aria-label="Использование по моделям">
  <caption class="sr-only">Расходы и статистика по AI-моделям</caption>
  ...
</table>

<table aria-label="История запросов">
  <caption class="sr-only">Хронология AI-запросов с фильтрацией по дате</caption>
  ...
</table>
```

### Priority 6 — Increase Touch Targets

```css
/* Export and control buttons */
.export-btn {
  min-height: 44px;
  padding: .625rem .9rem;  /* was .35rem */
}

/* Banner close */
.banner-close {
  min-width: 44px;
  min-height: 44px;
  padding: .5rem;
}

/* Bell */
.nav-bell {
  min-width: 44px;
  min-height: 44px;
}

/* Trend toggle */
.trend-toggle button {
  min-height: 36px;  /* minimum acceptable for grouped controls */
  padding: .5rem 1rem;
}
```

### Priority 7 — Focus Visible Indicator

```css
/* Replace outline:none with focus-visible */
.glass-input:focus-visible {
  outline: 2px solid #7dd3fc;
  outline-offset: 2px;
}

/* Global focus-visible for interactive elements */
button:focus-visible,
a:focus-visible,
[role="button"]:focus-visible {
  outline: 2px solid #7dd3fc;
  outline-offset: 2px;
  border-radius: 4px;
}
```

### Priority 8 — Trend Toggle: Add `role="group"` and Keyboard Navigation

```html
<div class="trend-toggle" id="trend-toggle"
     role="group"
     aria-label="Метрика тренда">
  <button class="active" data-metric="cost" aria-pressed="true">Cost</button>
  <button data-metric="calls" aria-pressed="false">Calls</button>
  <button data-metric="tokens" aria-pressed="false">Tokens</button>
</div>
```

Update JS to set `aria-pressed` on click and add arrow-key navigation within the group.

---

## Notes

- **Light theme:** `[data-theme="light"]` overrides all accent colors. Light theme contrasts were not calculated in this audit but are expected to pass given the stronger saturation values chosen (e.g. `#0284c7` on `#f5f7fb`). A separate verification pass is recommended.
- **Chart.js accessibility:** Chart.js 4.x does not automatically generate accessible alternatives. The `aria-label` on `<canvas>` provides a minimal accessible name but does not expose data values. Implementing a hidden `<table>` alongside each chart is the recommended pattern.
- **Animations:** `@keyframes pulse` on `.status-dot` and `@keyframes shimmer` on `.skeleton` — users with `prefers-reduced-motion` receive no accommodation. Add `@media (prefers-reduced-motion: reduce) { .status-dot, .skeleton { animation: none; } }`.
