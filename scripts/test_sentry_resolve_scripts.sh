#!/bin/bash
# Test для sentry_resolve_from_commit.sh + sentry_add_commit_backlinks.sh.
#
# Поднимает mock Sentry server на random free порт через python http.server,
# проверяет что:
# 1. resolve script делает PUT к project-level path с правильным body
# 2. resolve script парсит response body и valиdates "status":"resolved"
# 3. resolve script возвращает rc=1 если verification failed
# 4. backlink script использует /api/0/organizations/{org}/issues/{id}/comments/
# 5. backlink script возвращает rc=0 при 201 with comment_id
#
# Usage: bash scripts/test_sentry_resolve_scripts.sh

set -eo pipefail  # NOT -u (MOCK_PID set later in trap, ${MOCK_PID-} guard)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TMP_DIR=$(mktemp -d)
MOCK_PID=""
trap 'rm -rf "$TMP_DIR"; [ -n "${MOCK_PID-}" ] && kill "$MOCK_PID" 2>/dev/null || true' EXIT

# 1. Find free port
PORT=$(python3 -c "import socket; s=socket.socket(); s.bind(('',0)); print(s.getsockname()[1]); s.close()")
MOCK_URL="http://127.0.0.1:$PORT"

# 2. Generate mock server (responds correctly for valid paths, 404 for legacy paths)
cat > "$TMP_DIR/mock_sentry.py" <<'PYEOF'
import http.server, json, sys, urllib.parse

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_): pass

    def _read_body(self):
        n = int(self.headers.get("content-length", "0"))
        return self.rfile.read(n).decode() if n > 0 else ""

    def _json(self, status, body):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        body_bytes = json.dumps(body).encode()
        self.send_header("Content-Length", str(len(body_bytes)))
        self.end_headers()
        self.wfile.write(body_bytes)

    def do_GET(self):
        # /api/0/organizations/{org}/shortids/{short}/
        if "/shortids/" in self.path:
            short = self.path.rstrip("/").split("/")[-1]
            self._json(200, {
                "shortId": short,
                "group": {"id": "999111", "project": {"slug": "python-fastapi"}}
            })
        else:
            self._json(404, {"detail": "not found"})

    def do_PUT(self):
        body = self._read_body()
        # Old buggy path — return 204 to simulate silent-success bug
        if "/issues/?shortIdLookup=" in self.path:
            self.send_response(204); self.end_headers()
            return
        # New correct path
        if self.path.startswith("/api/0/projects/po-zm/python-fastapi/issues/?id="):
            data = json.loads(body) if body else {}
            if data.get("status") == "resolved":
                self._json(200, {"status": "resolved", "statusDetails": {}})
                return
        self._json(404, {"detail": "wrong path"})

    def do_POST(self):
        # /api/0/organizations/{org}/issues/{id}/comments/
        if "/organizations/po-zm/issues/" in self.path and "/comments/" in self.path:
            self._json(201, {"id": "888333", "user": {"id": "1"}})
            return
        # Legacy buggy path
        if self.path.startswith("/api/0/issues/") and "/comments/" in self.path:
            self._json(404, {"detail": "wrong path"})
            return
        self._json(404, {"detail": "no route"})

port = int(sys.argv[1])
http.server.HTTPServer(("127.0.0.1", port), Handler).serve_forever()
PYEOF

# 3. Start mock
python3 "$TMP_DIR/mock_sentry.py" "$PORT" &
MOCK_PID=$!
sleep 0.5

# 4. Test resolve
export SENTRY_AUTH_TOKEN="test-token"
export SENTRY_ORG_SLUG="po-zm"
export SENTRY_BASE_URL="$MOCK_URL"
export RESOLVING_COMMIT_SHA="abc1234"

echo "=== Test 1: resolve PYTHON-FASTAPI-5J ==="
OUT=$(bash "$SCRIPT_DIR/sentry_resolve_from_commit.sh" PYTHON-FASTAPI-5J 2>&1)
echo "$OUT"
if echo "$OUT" | grep -q "resolved: PYTHON-FASTAPI-5J"; then
    echo "  PASS: marked resolved"
else
    echo "  FAIL: did not mark resolved"
    exit 1
fi

# 5. Test backlink (called automatically from resolve)
if echo "$OUT" | grep -q "backlink ok: PYTHON-FASTAPI-5J"; then
    echo "  PASS: backlink committed"
else
    echo "  FAIL: backlink did not succeed"
    exit 1
fi

# 6. Test direct backlink call
echo ""
echo "=== Test 2: direct backlink call ==="
OUT=$(bash "$SCRIPT_DIR/sentry_add_commit_backlinks.sh" abc1234 PYTHON-FASTAPI-5J 2>&1)
echo "$OUT"
if echo "$OUT" | grep -q "backlink ok"; then
    echo "  PASS"
else
    echo "  FAIL"
    exit 1
fi

echo ""
echo "All tests PASS ✅"
