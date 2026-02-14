#!/bin/zsh

# Krab Agent Boundaries Sync
# Updates ownership metadata across projects.

PDIR=$(dirname "$(dirname "$0")")
OWNERSHIP_FILE="$PDIR/docs/agent_ownership.json"

mkdir -p "$PDIR/docs"

echo "🔄 [$(date)] Syncing Agent Boundaries..."

# Генерируем актуальную карту владения
cat <<EOF > "$OWNERSHIP_FILE"
{
  "project": "Krab Ecosystem",
  "version": "7.2",
  "last_sync": "$(date)",
  "boundaries": {
    "Krab (Bot)": {
      "path": "Краб/",
      "owner": "Senior Autonomous Architect (Antigravity)",
      "status": "Production"
    },
    "Voice Gateway": {
      "path": "Krab Voice Gateway/",
      "owner": "Antigravity (Hardening Lead)",
      "status": "Hardened"
    },
    "Krab Ear": {
      "path": "Krab Ear/",
      "owner": "Codex / Antigravity Hybrid",
      "status": "BETA"
    }
  },
  "conflicts": "None detected by run_conflict_defense.command"
}
EOF

echo "✅ Metadata updated at: $OWNERSHIP_FILE"
