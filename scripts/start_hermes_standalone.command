#!/usr/bin/env bash
# Hermes ACP standalone launcher (Wave 15-D, Phase 2 Phase A).
#
# Hermes ACP is a STDIO JSON-RPC protocol (per agent-client-protocol spec),
# NOT an HTTP service. There is no listening port. This launcher is intended
# to be invoked as a subprocess by the Phase B ACP bridge (Krab side), with
# stdin/stdout piped for ACP messaging and stderr captured for diagnostics.
#
# Usage:
#   bash scripts/start_hermes_standalone.command   # foreground (will wait on stdin)
#   echo '{"jsonrpc":"2.0",...}' | bash scripts/start_hermes_standalone.command
#
# Reads:
#   ~/.hermes/config.yaml   — primary config
#   ~/.hermes/.env          — API keys (loaded by hermes acp entrypoint)
#
# DOES NOT include Telegram MCP (Krab-only domain — avoids hallucination
# vector identified in Wave 9-B).

set -euo pipefail

HERMES_REPO="/Users/pablito/Antigravity_AGENTS/hermes-agent-eval"
HERMES_BIN="${HERMES_REPO}/.venv/bin/hermes"

if [[ ! -x "${HERMES_BIN}" ]]; then
  echo "[hermes-standalone] FATAL: hermes binary not found at ${HERMES_BIN}" >&2
  exit 1
fi

if [[ ! -d "${HOME}/.hermes" ]]; then
  echo "[hermes-standalone] FATAL: ~/.hermes/ not initialized" >&2
  exit 1
fi

cd "${HERMES_REPO}"
exec "${HERMES_BIN}" acp
