#!/bin/bash
# Resolve Sentry issues через project-level API по shortId.
#
# Usage:
#   sentry_resolve_from_commit.sh PYTHON-FASTAPI-5J [PYTHON-FASTAPI-6K …]
#
# Env:
#   SENTRY_AUTH_TOKEN   — обязателен (scope: event:write / project:write)
#   SENTRY_ORG_SLUG     — обязателен
#   SENTRY_PROJECT_SLUG — optional override (default: auto-detect через shortids GET, fallback "python-fastapi")
#   SENTRY_BASE_URL     — optional, default https://sentry.io
#   RESOLVING_COMMIT_SHA — optional commit для backlink (default: git rev-parse HEAD)
#
# Выход: 0 если все resolved, 1 при любой ошибке API/env.
#
# CRITICAL FIX (2026-04-25):
# Старая версия била PUT /api/0/organizations/{org}/issues/?shortIdLookup=1&query={shortId}
# который возвращал HTTP 204 даже когда 0 issues match → silent false-success.
# Новая версия: GET /shortids/{id}/ → project slug + numeric id, потом PUT
# /api/0/projects/{org}/{slug}/issues/?id={shortId} c body {"status":"resolved"}.
# Verifies response body содержит "status":"resolved" — иначе fail.

set -euo pipefail

if [ $# -eq 0 ]; then
    echo "sentry_resolve: no shortIds passed"
    exit 0
fi

TOKEN="${SENTRY_AUTH_TOKEN:-}"
ORG="${SENTRY_ORG_SLUG:-}"
DEFAULT_PROJECT="${SENTRY_PROJECT_SLUG:-}"
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

resolve_project_slug () {
    # GET /api/0/organizations/{org}/shortids/{shortId}/ → JSON c .group.project.slug
    # Returns project slug или empty if not found.
    local short_id="$1"
    local url="${BASE_URL}/api/0/organizations/${ORG}/shortids/${short_id}/"
    local resp
    resp=$(curl -sS -H "Authorization: Bearer $TOKEN" "$url" 2>/dev/null || echo "")
    echo "$resp" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    proj = (d.get('group') or {}).get('project') or {}
    print(proj.get('slug') or '')
except Exception:
    pass
" 2>/dev/null
}

for SHORT_ID in "$@"; do
    # 1. Resolve project slug (либо из env, либо auto-detect)
    if [ -n "$DEFAULT_PROJECT" ]; then
        PROJECT_SLUG="$DEFAULT_PROJECT"
    else
        PROJECT_SLUG=$(resolve_project_slug "$SHORT_ID")
        if [ -z "$PROJECT_SLUG" ]; then
            # Fallback: попробуй default python-fastapi
            PROJECT_SLUG="python-fastapi"
            echo "  warn: $SHORT_ID — project slug not detected, using fallback '$PROJECT_SLUG'"
        fi
    fi

    # 2. PUT project-level resolve
    URL="${BASE_URL}/api/0/projects/${ORG}/${PROJECT_SLUG}/issues/?id=${SHORT_ID}"
    BODY_FILE="/tmp/sentry_resolve_resp.$$"
    STATUS=$(curl -sS -o "$BODY_FILE" -w "%{http_code}" \
        -X PUT "$URL" \
        -H "Authorization: Bearer $TOKEN" \
        -H "Content-Type: application/json" \
        --data '{"status":"resolved"}' 2>/dev/null || echo "000")

    BODY=$(cat "$BODY_FILE" 2>/dev/null || echo "")
    rm -f "$BODY_FILE"

    case "$STATUS" in
        200|202)
            # 3. Verify body actually contains "status":"resolved"
            VERIFIED=$(echo "$BODY" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print('1' if d.get('status') == 'resolved' else '0')
except Exception:
    print('0')
" 2>/dev/null)
            if [ "$VERIFIED" = "1" ]; then
                echo "  resolved: $SHORT_ID (project=$PROJECT_SLUG)"
                RESOLVED_IDS+=("$SHORT_ID")
            else
                echo "  unverified: $SHORT_ID — HTTP $STATUS but body missing status:resolved (${BODY:0:120})"
                RC=1
            fi
            ;;
        404)
            echo "  not_found: $SHORT_ID (project=$PROJECT_SLUG, HTTP 404)"
            ;;
        *)
            echo "  error: $SHORT_ID — HTTP $STATUS ${BODY:0:200}"
            RC=1
            ;;
    esac
done

# Add backlink comment for resolved issues
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
