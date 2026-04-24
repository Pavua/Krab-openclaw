#!/bin/bash
# Smoke-test post-commit hook в изолированном tmp-репозитории.
#
# Стратегия:
# - создаём tmp git repo
# - копируем наш post-commit hook + sentry_resolve helper
# - mock Sentry API через local httpd (python http.server) на свободном порту
# - делаем fake commit с "Closes PYTHON-FASTAPI-TEST1"
# - проверяем, что в логе появились: commit=, push: disabled, sentry_auto_resolve
#
# Запуск: bash scripts/test_post_commit_hook.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP=$(mktemp -d -t krab_post_commit_XXXX)
LOG="$TMP/krab_post_commit.log"

cleanup() {
    # Останавливаем mock server если живой
    if [ -n "${MOCK_PID:-}" ]; then
        kill "$MOCK_PID" 2>/dev/null || true
    fi
    rm -rf "$TMP"
}
trap cleanup EXIT

echo "[test] tmp=$TMP"

# --- 1. Mock Sentry server ----------------------------------------
cat > "$TMP/mock_sentry.py" <<'PY'
import http.server, socketserver, sys, json
PORT = int(sys.argv[1])
class H(http.server.BaseHTTPRequestHandler):
    def do_PUT(self):
        length = int(self.headers.get("content-length", 0))
        _ = self.rfile.read(length) if length else b""
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status":"resolved"}')
    def log_message(self, *a, **kw):
        pass
with socketserver.TCPServer(("127.0.0.1", PORT), H) as s:
    s.serve_forever()
PY

# свободный порт
PORT=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')
python3 "$TMP/mock_sentry.py" "$PORT" &
MOCK_PID=$!
sleep 0.3

# --- 2. Init tmp git repo + копируем hook ------------------------
cd "$TMP"
git init -q .
git config user.email "test@krab.local"
git config user.name "krab-test"

mkdir -p .git/hooks scripts
cp "$REPO_ROOT/scripts/git_hooks/post-commit" .git/hooks/post-commit
cp "$REPO_ROOT/scripts/sentry_resolve_from_commit.sh" scripts/sentry_resolve_from_commit.sh
chmod +x .git/hooks/post-commit scripts/sentry_resolve_from_commit.sh

# Перенаправляем LOG в tmp (env-override через sed)
sed -i.bak "s|LOG=/tmp/krab_post_commit.log|LOG=$LOG|" .git/hooks/post-commit
rm -f .git/hooks/post-commit.bak

# --- 3. .env с fake Sentry config --------------------------------
cat > .env <<EOF
KRAB_AUTOPUSH=0
KRAB_POSTCOMMIT_E2E=0
SENTRY_AUTH_TOKEN=fake-token-for-test
SENTRY_ORG_SLUG=krab-test
SENTRY_BASE_URL=http://127.0.0.1:$PORT
EOF

# --- 4. Fake commit -----------------------------------------------
echo "hello" > README.md
git add README.md .env
git commit -q -m "fix(test): dummy change

Closes PYTHON-FASTAPI-TEST1
Also fixes PYTHON-FASTAPI-TEST2
" || { echo "[test] commit failed"; exit 1; }

sleep 0.3

# --- 5. Assertions ------------------------------------------------
echo ""
echo "[test] ---- log ----"
cat "$LOG"
echo "[test] --------------"

FAIL=0
grep -q "commit=" "$LOG" || { echo "[FAIL] no commit= line"; FAIL=1; }
grep -q "push: disabled" "$LOG" || { echo "[FAIL] push not skipped"; FAIL=1; }
grep -q "sentry_auto_resolve candidates" "$LOG" || { echo "[FAIL] no sentry candidates"; FAIL=1; }
grep -q "PYTHON-FASTAPI-TEST1" "$LOG" || { echo "[FAIL] shortId missing"; FAIL=1; }
grep -q "resolved: PYTHON-FASTAPI-TEST1" "$LOG" || { echo "[FAIL] mock resolve not logged"; FAIL=1; }

if [ "$FAIL" -eq 0 ]; then
    echo "[test] PASS"
    exit 0
else
    echo "[test] FAIL"
    exit 1
fi
