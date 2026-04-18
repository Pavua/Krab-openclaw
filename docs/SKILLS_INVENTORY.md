# Krab Skills + Plugins Inventory (18.04.2026)

## src/skills/ (4 skills, 577 LoC)

| Skill | LoC | Purpose | Status |
|-------|-----|---------|--------|
| mercadona.py | 464 | Playwright web scraper for Mercadona supermarket product search + pricing | Active (async, stealth mode) |
| web_search.py | 47 | Brave API integration for AI-powered search with summaries | Active |
| imessage.py | 45 | iMessage integration for message sending/receiving | Active |
| crypto.py | 21 | Crypto utilities (encoding/decoding, hashing) | Active |

## plugin.json files

**Location:** None found. Krab does not use plugin.json structure in codebase.

## MCP servers (from ~/.claude.json)

### Global MCP registry

| Name | Type | Disabled | Purpose |
|------|------|----------|---------|
| openclaw-browser | stdio (npx chrome-devtools-mcp) | **true** | CDP Chrome for browser automation |
| chrome-devtools | stdio (npx chrome-devtools-mcp) | **true** | Chrome DevTools MCP (isolated) |
| playwright | stdio (npx @playwright/mcp) | **true** | Playwright automation MCP |

### LaunchAgents (macOS services via plist)

| Service | Port | Plist | Status | Purpose |
|---------|------|-------|--------|---------|
| OpenClaw gateway | 18789 | — | Active | Native OpenClaw chat/agent runtime |
| MCP yung-nagato (Krab on Yung) | 8011 | `com.krab.mcp-yung-nagato.plist` | **Active** | Telegram userbot MCP (SSE) |
| MCP p0lrd (main Krab) | 8012 | `com.krab.mcp-p0lrd.plist` | **Active** | Telegram owner main MCP (SSE) |
| MCP Hammerspoon | 8013 | `com.krab.mcp-hammerspoon.plist` | **Active** | macOS automation MCP |
| Inbox watcher | — | `ai.krab.inbox-watcher.plist` | **Active** | Background inbox item processor |
| Krab core | — | — | **Active** | Main userbot runtime (ai.krab.core) |
| KrabEar backend | — | — | **Active** | Voice transcription (ai.krab.ear.backend) |

## Key findings

### ✓ Active & clean
- **Skills:** All 4 skills (mercadona, web_search, imessage, crypto) are active and used
- **MCP servers:** 4 LaunchAgents running + OpenClaw gateway (enabled in runtime)
- **Plugin isolation:** Krab avoids plugin.json bloat; uses direct LaunchAgent/plist registration

### ⚠ Disabled globally (not in project scope)
- 3 MCP servers disabled in ~/.claude.json: `openclaw-browser`, `chrome-devtools`, `playwright`
- Reason: Not needed for Krab's CLI/automation model (browser control via pyrofork + CDP bridge instead)

### 📊 Inventory summary
- **Total skills:** 4 (all active)
- **Total plugin.json files:** 0
- **Total MCP servers (global):** 3 disabled + 4 LaunchAgents active = 7 total
- **Active services:** 6 (yung-nagato, p0lrd, hammerspoon, inbox-watcher, core, krab-ear)

## Recommendations

### No cleanup needed
Krab's plugin/skill footprint is minimal and intentional. All entries are documented in CLAUDE.md § Инфраструктура.

### Monitor
- Mercadona scraper: Depends on live web flow (no API stability guarantee); flag in backlog if site flow changes
- Voice Gateway (KrabEar): Currently running but needs integration validation (phase 11 work)
