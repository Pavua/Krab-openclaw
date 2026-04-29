#!/bin/bash
# ============================================================================
# Tests for scripts/sentry_auto_rollback.sh
# ----------------------------------------------------------------------------
# Isolated tests with mocked Sentry API, mocked git, mocked curl/TG.
# Запуск: bash scripts/test_auto_rollback.sh
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="$SCRIPT_DIR/sentry_auto_rollback.sh"

PASS=0
FAIL=0
CURRENT=""

setup() {
    CURRENT="$1"
    TMP=$(mktemp -d)
    export KRAB_REPO="$TMP/repo"
    export KRAB_ENV_FILE="$TMP/.env"
    export KRAB_AUTO_ROLLBACK_LOG="$TMP/run.log"
    export KRAB_AUTO_ROLLBACK_STATE="$TMP/state.ts"
    export KRAB_AUTO_ROLLBACK_ABORT_FLAG="$TMP/abort.flag"
    export KRAB_AUTO_ROLLBACK_ALERT_WAIT_SEC=1
    export KRAB_AUTO_ROLLBACK_ENABLED=1
    export SENTRY_AUTH_TOKEN="fake-token"
    export SENTRY_ORG=x SENTRY_PROJECT=y
    export TELEGRAM_BOT_TOKEN=""  # suppress real TG
    export TELEGRAM_OWNER_CHAT_ID=""

    # Git repo with a fresh commit
    mkdir -p "$KRAB_REPO"
    (cd "$KRAB_REPO" && git init -q && git config user.email t@t && git config user.name t \
        && echo a > f && git add f && git commit -q -m "initial commit")

    # Mock bin
    MOCK_BIN="$TMP/bin"
    mkdir -p "$MOCK_BIN"
    export PATH="$MOCK_BIN:$PATH"
    export MOCK_DIR="$TMP"
    echo 0 > "$TMP/revert_calls"
    echo 0 > "$TMP/push_calls"

    # Mock curl — reads $TMP/sentry_response for Sentry calls, noop for TG
    cat > "$MOCK_BIN/curl" <<'EOF'
#!/bin/bash
for arg in "$@"; do
    if [[ "$arg" == *"telegram"* ]]; then exit 0; fi
done
cat "$MOCK_DIR/sentry_response" 2>/dev/null || echo "[]"
EOF
    chmod +x "$MOCK_BIN/curl"

    # Wrap git to count revert/push
    real_git=$(which git)
    cat > "$MOCK_BIN/git" <<EOF
#!/bin/bash
if [[ "\$1" == "revert" ]]; then
    n=\$(cat "$TMP/revert_calls"); echo \$((n+1)) > "$TMP/revert_calls"
    exec $real_git commit --allow-empty -m "revert mock"
fi
if [[ "\$1" == "push" ]]; then
    n=\$(cat "$TMP/push_calls"); echo \$((n+1)) > "$TMP/push_calls"
    exit 0
fi
exec $real_git "\$@"
EOF
    chmod +x "$MOCK_BIN/git"
}

teardown() { rm -rf "$TMP"; }

assert() {
    if eval "$1"; then
        echo "  PASS: $CURRENT :: $2"; PASS=$((PASS+1))
    else
        echo "  FAIL: $CURRENT :: $2"; FAIL=$((FAIL+1))
    fi
}

# --- Test 1: High issue count → revert invoked -----------------------------
setup "high_count_triggers_revert"
printf '[%s]' "$(printf '{},%.0s' {1..20} | sed 's/,$//')" > "$TMP/sentry_response"
bash "$TARGET" || true
reverts=$(cat "$TMP/revert_calls")
assert "[[ $reverts -eq 1 ]]" "revert invoked on spike (got $reverts)"
assert "[[ -f $KRAB_AUTO_ROLLBACK_STATE ]]" "state file written"
teardown

# --- Test 2: Low issue count → no action -----------------------------------
setup "low_count_no_action"
echo '[{},{}]' > "$TMP/sentry_response"
bash "$TARGET" || true
reverts=$(cat "$TMP/revert_calls")
assert "[[ $reverts -eq 0 ]]" "no revert under threshold (got $reverts)"
teardown

# --- Test 3: Abort flag → cancel -------------------------------------------
setup "abort_flag_cancels"
printf '[%s]' "$(printf '{},%.0s' {1..20} | sed 's/,$//')" > "$TMP/sentry_response"
# set wait to 2s and fire the flag mid-way via background process
export KRAB_AUTO_ROLLBACK_ALERT_WAIT_SEC=3
( sleep 1; touch "$KRAB_AUTO_ROLLBACK_ABORT_FLAG" ) &
bash "$TARGET" || true
wait
reverts=$(cat "$TMP/revert_calls")
assert "[[ $reverts -eq 0 ]]" "abort flag cancels revert (got $reverts)"
teardown

# --- Test 4: Rate limit (2nd run within hour) ------------------------------
setup "rate_limit_blocks_second"
printf '[%s]' "$(printf '{},%.0s' {1..20} | sed 's/,$//')" > "$TMP/sentry_response"
date +%s > "$KRAB_AUTO_ROLLBACK_STATE"  # pretend we just reverted
bash "$TARGET" || true
reverts=$(cat "$TMP/revert_calls")
assert "[[ $reverts -eq 0 ]]" "rate-limit blocks 2nd revert (got $reverts)"
teardown

# --- Test 5: [skip-autorevert] escape hatch --------------------------------
setup "skip_autorevert_tag"
(cd "$KRAB_REPO" && git commit --allow-empty -m "deploy [skip-autorevert]")
printf '[%s]' "$(printf '{},%.0s' {1..20} | sed 's/,$//')" > "$TMP/sentry_response"
bash "$TARGET" || true
reverts=$(cat "$TMP/revert_calls")
assert "[[ $reverts -eq 0 ]]" "skip-autorevert tag respected (got $reverts)"
teardown

# --- Test 6: Disabled by default (env=0) -----------------------------------
setup "disabled_when_env_zero"
export KRAB_AUTO_ROLLBACK_ENABLED=0
printf '[%s]' "$(printf '{},%.0s' {1..20} | sed 's/,$//')" > "$TMP/sentry_response"
bash "$TARGET" || true
reverts=$(cat "$TMP/revert_calls")
assert "[[ $reverts -eq 0 ]]" "disabled env → no action (got $reverts)"
teardown

echo
echo "Results: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
