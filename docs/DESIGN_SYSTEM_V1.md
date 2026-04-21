# Krab Design System v1.0

> Authored: 2026-04-21
> Co-authors: Claude Code (Sonnet 4.6) + Chado (@callme_chado, Claude Code robot)
> Status: Locked — governs all 3 v2 architecture artifacts

---

## 1. Overview

The Krab visual language spans three artifacts, each targeting a distinct audience
and communication goal. Together they form a coherent system: same shapes, same
arrows, same key — reader never relearns between artifacts.

### Artifact hierarchy

| Artifact | Audience | Format | Tool |
|----------|----------|--------|------|
| Hero / Vision | README cover, onboarding, social preview | 1920 × 1080 poster | Canva |
| Engineering / Runtime | Developers, code review, onboarding | 2400 × 1200 interactive HTML | Claude Design |
| Ops / Truth | On-call, incident response, daily health | 2000 × 1200 dashboard | Claude Design |

### Authorship note

The cross-artifact invariants in this document — shape=kind enforcement, arrow
vocabulary reduced to max 3, failure as toggle overlay, source-of-truth badges
restricted to stores only, and the shared key concept — were contributed by
Chado (@callme_chado) during an AI-to-AI design review on 2026-04-19 (Session 16).
This is the first cross-AI design collaboration on the Krab project and sets
a precedent for structured peer review between autonomous agents.

---

## 2. Shape Invariant

A reader who sees a shape in Hero must immediately recognise the same kind in
Engineering and Ops without consulting the legend. Shape encodes semantic kind —
not visual decoration, not layer position.

| Kind | Shape | Rule |
|------|-------|------|
| Actor (human) | Circle / avatar | People only. Owner @p0lrd, Swarm accounts, Telegram contacts. |
| Process (service) | Flat rectangle | Any Python or Node.js runtime component. No rounded corners. |
| Store (data) | Cylinder | Persistent storage only. The only shape that carries SoT badges. |
| External (cloud) | Hexagon | Third-party APIs and cloud providers. Never drawn as rectangles. |
| Schedule (cron/routine) | Clock or calendar icon | LaunchAgents, Desktop Routines, recurrent timers. |

This invariant was introduced by Chado as a cross-artifact enforcement rule.
It supercedes the earlier v1 skeleton that used rounded rectangles for UI
surfaces and parallelograms for queues — those distinctions are dropped to
keep the system learnable in a single glance.

---

## 3. Shared Key

One legend is printed once — in the lower corner of the Hero artifact.
Engineering and Ops link to it rather than repeating it. This prevents
legend drift and ensures a change to the system propagates from a single
source.

The key contains:

- Shape table (§2 above, 5 rows)
- Arrow table (§4 below, 3 rows)
- Color palette for ownership boundaries (§7 below, 6 rows)
- SoT badge table (§6 below, 5 rows)

The concept of a single shared key across all three artifacts was contributed by Chado.

---

## 4. Arrow Vocabulary (max 3)

No artifact uses more than 3 arrow types. This constraint was set by Chado
to prevent glyph noise from drowning the happy path. The earlier v1 skeleton
had 6 arrow types (including async green, bidirectional double, and ambient
grey); all have been merged or removed.

| Type | Color | Style | Meaning |
|------|-------|-------|---------|
| data | Cyan `#00D4FF` | Solid line, filled arrowhead | Sync call, async event, data flow — unified |
| control | Purple `#9B59FF` | Dashed line, diamond terminus | Policy signal, ACL gate, toggle, schedule trigger |
| failure | Red `#FF4444` | Solid line, filled arrowhead | Fallback path, error trigger, alert escalation |

The failure arrow appears only in the toggle overlay layer (see §5). It is
never drawn inline with the happy path on the base view.

Arrow pruning rule: before adding any arrow, ask whether removing it breaks
the reader's understanding of the narrative. If no — remove it. Config reads,
internal imports, and logging sinks are ambient and are never drawn.

---

## 5. Failure Layer as Toggle Overlay

Failure paths are a separate visual layer, not a parallel track next to the
happy path. The base view of Engineering and Ops shows happy path only
(cyan and purple arrows). Failure paths are activated by a toggle button or
click interaction; they overlay the base canvas with red arrows and failure
labels without moving or obscuring the primary flow.

This separation was Chado's strongest structural contribution. The motivation:
when failure paths are drawn adjacent to the happy path, glyph density
destroys comprehension of the primary flow for new readers. An overlay keeps
the base view clean while making failure visibility opt-in with a single click.

Implementation in Claude Design artifacts: an HTML checkbox or button toggles
a CSS class that sets `display: none` on all `.failure-path` elements. Default
state is hidden.

In the Ops artifact, the red alert overlay (P7 panel) follows the same rule —
alert paths are a toggle, not the default view.

---

## 6. Source-of-Truth Markers

SoT badges are applied exclusively to cylinders (stores). Processes are
trust-throughput components — they route and transform data, but they are
not the authoritative source of it. Placing TRUTH badges on services was a
v1 error corrected by Chado's review.

| Badge | Color | Meaning | Applied to |
|-------|-------|---------|-----------|
| TRUTH | Gold `#FFD700` | Authoritative — runtime reads from here | `archive.db`, `models.json` |
| CACHE | Blue `#4488FF` | Derived, TTL-bound, regenerable | `swarm_memory_*.json`, `history_cache` |
| DERIVED | Teal `#44BBAA` | Computed from TRUTH, eventually consistent | `krab_llm_route_ok` metric |
| OBSERVED | Orange `#FF8800` | Runtime sampled, not persisted on restart | `krab_runtime_state/`, ChatWindow buffers |
| STALE-PRONE | Red outline | Known to drift, needs manual reconcile | `CLAUDE.md`, `next_session.md` |

Example of correct usage: `archive.db` (cylinder) carries TRUTH. The Memory
Indexer worker (flat rectangle) that writes to it carries no badge —
it is a process.

---

## 7. Ownership Boundaries

Each major subsystem is enclosed in a dashed-border group box with a team
label. The box uses a tinted fill at 15% opacity so it reads as a region
without obscuring the nodes inside.

| Boundary | Fill | Label | Contents |
|----------|------|-------|---------|
| Krab repo `src/` | `#1A2840` navy | Krab Python Runtime | All Python modules |
| `~/.openclaw` runtime | `#1A3028` pine | OpenClaw Gateway | Gateway process, models.json, krab_runtime_state/ |
| External services | `#2A1A2E` plum | External Cloud | Gemini, Claude, OpenAI, Brave Search |
| Telegram network | `#1A2550` midnight | Telegram Surface | MTProto endpoints, Swarm accounts |
| Owner UI | `#2A2010` amber | Owner Panel | Panel :8080, MCP servers :8011-8013 |
| macOS host | `#1A1A1A` dark grey | macOS / launchd | LaunchAgents, Hammerspoon, LM Studio |

Boundaries must not overlap except at intentional integration points (e.g.,
the MCP SSE stream crosses the Krab and Owner boundaries by design).

---

## 8. Canvas Specifications

| Artifact | Canvas size | Orientation | Export targets |
|----------|------------|-------------|----------------|
| Hero / Vision | 1920 × 1080 px | Landscape 16:9 | PNG for README cover, GitHub social preview, A3 print |
| Engineering / Runtime | 2400 × 1200 px | Wide landscape | HTML (interactive, scroll-horizontal) |
| Ops / Truth | 2000 × 1200 px | Wide landscape | HTML (dashboard grid), print variant (`-print.html`) |

Background color for all three: `#0D1117` (navy midnight).
Font: Inter — bold for node labels, regular for subtitles and values,
mono for metrics and file paths.
Maximum label length per node: 3 words (Hero), 5 words (Engineering/Ops).

---

## 9. Tooling

| Artifact | Tool | Rationale |
|----------|------|-----------|
| Hero / Vision | Canva | Fastest iteration for infographic-style recaps and social posts; 4 candidate variants in ~30 seconds; suitable when technical precision is secondary |
| Engineering / Runtime | Claude Design (HTML) | Technical blueprint with interactive hover details, toggle overlays, precise shape and arrow control, real component data |
| Ops / Truth | Claude Design (HTML) | Dashboard grid layout with status dots, sparklines, and print-ready variant; Grafana-style aesthetic |

A/B test result from Session 16: Claude Design produces a single polished
technical blueprint with real data representation in ~3 minutes. Canva produces
4 generic infographic candidates in ~30 seconds. Claude Design wins for
technical artifacts; Canva is retained for quick session recaps and
social posts.

---

## 10. Refresh Cadence

All three artifacts are refreshed monthly via the `krab-openclaw-monthly-arch`
Desktop Routine, triggered on the 1st of each month.

The routine updates the following metrics in the Canva Hero export:

- Total commits
- API endpoints count
- Archive DB size and message count
- Last Session number

After update: export PNG, commit to `docs/artifacts/architecture_YYYY-MM.png`.

Engineering and Ops artifacts in Claude Design are refreshed manually when
a significant architecture change lands (new swimlane, new store, new team boundary).
Threshold: any change that would require adding or removing a node,
not just updating a number.

---

## 11. Links

### v2 artifacts (current — 2026-04-21)

Hero / Vision (Canva):

- Primary variant: https://www.canva.com/d/wDX_xg3mClWE0t7
- Alt variant: https://www.canva.com/d/rZnjP7YLE8JzKaY

Engineering / Runtime (Claude Design):

- https://claude.ai/design/p/f8108663-9376-444f-8c2c-1e93302a02d6?file=Krab+Architecture.html

Ops / Truth (Claude Design + Canva mirror):

- Claude Design: https://claude.ai/design/p/90d6069e-1ccf-4144-863d-db54f3c08685?file=Ops+Dashboard.html
- Canva mirror (editable): https://www.canva.com/d/3dkWS667S3h08UB

### v1 artifacts (archived — 2026-04-20, Session 16)

- Claude Design: https://claude.ai/design/p/11567838-b049-4ab7-b217-83103fe5ec68?file=Krab+Architecture.html
- Canva: https://www.canva.com/d/22tGJQlMa2UbXyi

---

*Design System v1.0 — locked 2026-04-21.*
*Source: `docs/ARCHITECTURE.md`, `docs/ARCHITECTURE_V2_SKELETON.md`, `docs/CHADO_INSIGHTS.md` §8.*
*Next review: 2026-05-01 (monthly-arch routine) or on next major architecture change.*
