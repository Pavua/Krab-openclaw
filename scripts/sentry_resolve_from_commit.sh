#!/bin/bash
# Resolve Sentry issues через API по shortId (например, PYTHON-FASTAPI-5J).
#
# Usage:
#   sentry_resolve_from_commit.sh PYTHON-FASTAPI-5J [PYTHON-FASTAPI-6K …]
#
# Env:
#   SENTRY_AUTH_TOKEN — обязателен (scope: event:write / project:write)
#   SENTRY_ORG_SLUG   — обязателен (org slug из URL sentry.io/organizations/<slug>/)
#   SENTRY_BASE_URL   — optional, default https://sentry.io (override для тестов)
#
# Выход: 0 если все resolved (или не было id), 1 при любой ошибке API/env.

set -euo pipefail

if [ $# -eq 0 ]; then
    echo "sentry_resolve: no shortIds passed"
    exit 0
fi

TOKEN="${SENTRY_AUTH_TOKEN:-}"
ORG="${SENTRY_ORG_SLUG:-}"
BASE_URL="${SENTRY_BASE_URL:-https://sentry.io}"

if [ -z "$TOKEN" ]; then
    echo "sentry_resolve: SENTRY_AUTH_TOKEN not set — skip"
    exit 0
fi
if [ -z "$ORG" ]; then
    echo "sentry_resolve: SENTRY_ORG_SLUG not set — skip"
    exit 0
fi

RC=0
RESOLVED_IDS=()

for SHORT_ID in "$@"; do
    # PUT /api/0/organizations/{org}/issues/?shortIdLookup=1 — resolve by shortId
    URL="${BASE_URL}/api/0/organizations/${ORG}/issues/?shortIdLookup=1&query=${SHORT_ID}"
    RESP=$(curl -sS -w "\nHTTP_STATUS:%{http_code}" \
        -X PUT "$URL" \
        -H "Authorization: Bearer $TOKEN" \
        -H "Content-Type: application/json" \
        --data '{"status":"resolved"}' 2>&1 || echo "HTTP_STATUS:curl_err")

    STATUS=$(echo "$RESP" | tail -n1 | sed -e 's/.*HTTP_STATUS://')
    BODY=$(echo "$RESP" | sed -e 's/HTTP_STATUS:.*//')

    case "$STATUS" in
        200|202|204)
            echo "  resolved: $SHORT_ID"
            RESOLVED_IDS+=("$SHORT_ID")
            ;;
        404)
            # shortId не найден в org — не считаем фатальной ошибкой
            echo "  not_found: $SHORT_ID (HTTP 404)"
            ;;
        *)
            echo "  error: $SHORT_ID — HTTP $STATUS ${BODY:0:200}"
            RC=1
            ;;
    esac
done

# Добавляем backlink-комментарий в resolved issues с git SHA (если задан).
COMMIT_SHA="${RESOLVING_COMMIT_SHA:-}"
if [ -z "$COMMIT_SHA" ] && command -v git >/dev/null 2>&1; then
    COMMIT_SHA=$(git rev-parse HEAD 2>/dev/null || true)
fi

if [ ${#RESOLVED_IDS[@]} -gt 0 ] && [ -n "$COMMIT_SHA" ]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    BACKLINK="${SCRIPT_DIR}/sentry_add_commit_backlinks.sh"
    if [ -x "$BACKLINK" ]; then
        "$BACKLINK" "$COMMIT_SHA" "${RESOLVED_IDS[@]}" || RC=1
    fi
fi

exit $RC
