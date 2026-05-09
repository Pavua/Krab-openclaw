#!/bin/bash
# Wave 49-C-hexstrike: manual toggle for HexStrike-AI server (151 offensive sec tools).
# Owner-gated: explicit confirmation required before loading. Server runs on :8888.
#
# Risk model: HexStrike orchestrates aggressive scanning tools (nmap, sqlmap, hydra,
# nuclei, etc). Auto-start would be a major safety violation — operator must
# explicitly invoke this script and confirm.

set -u

PLIST_NAME="com.krab.hexstrike-server"
PLIST_SRC="$(cd "$(dirname "$0")/launchagents" && pwd)/${PLIST_NAME}.plist"
PLIST_DST="${HOME}/Library/LaunchAgents/${PLIST_NAME}.plist"
HEALTH_URL="http://127.0.0.1:8888/health"

is_loaded() {
  launchctl list 2>/dev/null | grep -q "${PLIST_NAME}"
}

server_status() {
  local code
  code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 3 "${HEALTH_URL}" 2>/dev/null || echo "000")
  if [[ "${code}" == "200" ]]; then
    echo "RUNNING (HTTP 200 on :8888)"
  else
    echo "NOT REACHABLE (curl ${code})"
  fi
}

ensure_plist_installed() {
  if [[ ! -f "${PLIST_SRC}" ]]; then
    echo "ERROR: source plist missing at ${PLIST_SRC}" >&2
    exit 1
  fi
  mkdir -p "${HOME}/Library/LaunchAgents"
  cp "${PLIST_SRC}" "${PLIST_DST}"
}

action_load() {
  ensure_plist_installed
  launchctl load -w "${PLIST_DST}"
  sleep 2
  echo "Status: $(server_status)"
}

action_unload() {
  if [[ -f "${PLIST_DST}" ]]; then
    launchctl unload "${PLIST_DST}" 2>/dev/null || true
  fi
  echo "HexStrike server stopped."
}

# Interactive prompt via osascript (macOS GUI dialog).
prompt_choice() {
  local current_state
  if is_loaded; then
    current_state="LOADED"
  else
    current_state="UNLOADED"
  fi
  osascript <<APPLESCRIPT 2>/dev/null
set theChoice to button returned of (display dialog ¬
  "HexStrike-AI server (offensive security tools, port 8888)\n\nCurrent state: ${current_state}\nServer status: $(server_status)\n\nIMPORTANT: Only load when actively running an authorized engagement." ¬
  buttons {"Cancel", "Unload", "Load"} ¬
  default button "Cancel" ¬
  with title "Krab — HexStrike Toggle")
return theChoice
APPLESCRIPT
}

main() {
  local choice
  choice=$(prompt_choice)
  case "${choice}" in
    Load)
      action_load
      ;;
    Unload)
      action_unload
      ;;
    *)
      echo "Cancelled. No changes."
      ;;
  esac
}

main "$@"
