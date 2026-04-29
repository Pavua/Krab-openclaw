#!/bin/bash
# Добавляет comment в Sentry issue с ссылкой на git commit, который её закрыл.
#
# Usage:
#   sentry_add_commit_backlinks.sh <commit_sha> <SHORT_ID> [SHORT_ID …]
#
# Env:
#   SENTRY_AUTH_TOKEN — обязателен (scope: event:write / project:write)
#   SENTRY_ORG_SLUG   — обязателен (org slug)
#   SENTRY_BASE_URL   — optional, default https://sentry.io
#   GITHUB_REPO       — optional, default Pavua/Krab-openclaw
#
# Выход: 0 если все backlinks добавлены (или skip по env), 1 при любой ошибке API.

set -euo pipefail

if [ $# -lt 2 ]; then
    echo "Usage: $0 <commit_sha> <SHORT_ID> [SHORT_ID ...]" >&2
    exit 2
fi

COMMIT_SHA="$1"
shift

TOKEN="${SENTRY_AUTH_TOKEN:-}"
ORG="${SENTRY_ORG_SLUG:-}"
BASE_URL="${SENTRY_BASE_URL:-https://sentry.io}"
REPO="${GITHUB_REPO:-Pavua/Krab-openclaw}"

if [ -z "$TOKEN" ] || [ -z "$ORG" ]; then
    echo "sentry_backlink: SENTRY_AUTH_TOKEN/ORG_SLUG not set — skip"
    exit 0
fi

COMMIT_URL="https://github.com/${REPO}/commit/${COMMIT_SHA}"
RC=0

resolve_issue_id () {
    local short_id="$1"
    local url="${BASE_URL}/api/0/organizations/${ORG}/shortids/${short_id}/"
    local resp
    resp=$(curl -sS -H "Authorization: Bearer $TOKEN" "$url" 2>/dev/null || echo "")
    # Извлекаем "group":{"id":"..."} — minimal jq-free parsing.
    echo "$resp" | python3 -c "import sys, json
try:
    d = json.load(sys.stdin)
    g = d.get('group') or {}
    print(g.get('id') or '')
except Exception:
    pass" 2>/dev/null
}

for SHORT_ID in "$@"; do
    ISSUE_ID=$(resolve_issue_id "$SHORT_ID")
    if [ -z "$ISSUE_ID" ]; then
        echo "  backlink skip: $SHORT_ID (issue_id not resolved)"
        continue
    fi

    PAYLOAD=$(python3 -c "import json,sys; print(json.dumps({'text': f'Fixed in commit {sys.argv[1]}: {sys.argv[2]}'}))" \
        "$COMMIT_SHA" "$COMMIT_URL")

    # FIX (2026-04-25): /api/0/issues/{id}/comments/ returns 404.
    # Correct path is /api/0/organizations/{org}/issues/{id}/comments/ → 201.
    STATUS=$(curl -sS -o /tmp/sentry_backlink_resp.$$ -w "%{http_code}" \
        -X POST "${BASE_URL}/api/0/organizations/${ORG}/issues/${ISSUE_ID}/comments/" \
        -H "Authorization: Bearer $TOKEN" \
        -H "Content-Type: application/json" \
        -d "$PAYLOAD" 2>/dev/null || echo "000")

    case "$STATUS" in
        200|201|202)
            # Verify response body — should contain "id" field (created comment id)
            COMMENT_ID=$(python3 -c "
import sys, json
try:
    with open('/tmp/sentry_backlink_resp.$$') as f:
        d = json.load(f)
    print(d.get('id') or '')
except Exception:
    pass
" 2>/dev/null)
            if [ -n "$COMMENT_ID" ]; then
                echo "  backlink ok: $SHORT_ID → $COMMIT_SHA (comment_id=$COMMENT_ID)"
            else
                echo "  backlink unverified: $SHORT_ID HTTP $STATUS but no comment_id in response"
                RC=1
            fi
            ;;
        *)
            BODY=$(head -c 200 /tmp/sentry_backlink_resp.$$ 2>/dev/null || true)
            echo "  backlink error: $SHORT_ID — HTTP $STATUS $BODY"
            RC=1
            ;;
    esac
    rm -f /tmp/sentry_backlink_resp.$$
done

exit $RC
