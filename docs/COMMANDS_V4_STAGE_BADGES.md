# Commands V4 Stage Badges — Live Validation

Date: 2026-04-21

## API `/api/commands` — stage field presence

All 151 commands return a `stage` field. No missing values.

| Stage | Count |
|------|-------|
| production | 150 |
| beta | 1 |
| experimental | 0 |

## HTML `/v4/commands` — badge CSS classes

Present in page CSS (lines 54-56):
- `.badge-stage-production` — green tint
- `.badge-stage-beta` — yellow tint
- `.badge-stage-experimental` — red tint

Badge rendering is JS-driven: `fetch('/api/commands')` → `badge-stage-{stage}` span injected per row (lines 664-669). Stage filter dropdown present at `#filter-stage`.

## Verdict

All green. W7.2 stage badges render correctly:
- API returns `stage` on every command
- CSS classes defined for all 3 stages
- JS renderer uses `cmd.stage || 'production'` fallback
- Stage column sortable, filter dropdown functional
