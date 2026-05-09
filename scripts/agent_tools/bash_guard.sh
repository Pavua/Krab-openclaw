#!/bin/bash
# Wave 44-S-safety-net: pre-execution safety wrapper для bash-команд агента.
#
# Layered defense (НЕ replaces codex sandbox config):
#   1. BLOCK    — заведомо разрушительные операции (rm -rf /, fork bombs, etc.)
#   2. CONFIRM  — операции требующие явного owner-token (mass delete, force push)
#   3. ALLOW    — всё остальное, audit-логируется
#
# Usage:
#   bash_guard.sh --cmd "ls /tmp"                       # exec, exit 0
#   bash_guard.sh --cmd "rm -rf /"                       # block, exit 78
#   bash_guard.sh --cmd "git push --force" \             # confirm needed, exit 79
#       --owner-confirm-token <token>                    # OR exec if token valid
#
# Exit codes:
#   0   — executed successfully
#   78  — blocked (EX_CONFIG-ish, irrecoverable)
#   79  — requires owner confirm (token absent/invalid)
#   *   — passthrough exit code from executed command

set -u

AUDIT_LOG="/tmp/krab_bash_audit.log"
AUDIT_MAX_BYTES=$((10 * 1024 * 1024))  # 10MB rotate
TOKEN_PATH="/Users/pablito/.openclaw/krab_runtime_state/owner_confirm.token"

CMD=""
OWNER_TOKEN=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --cmd)
            CMD="$2"
            shift 2
            ;;
        --owner-confirm-token)
            OWNER_TOKEN="$2"
            shift 2
            ;;
        *)
            echo "{\"ok\":false,\"error\":\"unknown_arg: $1\"}" >&2
            exit 78
            ;;
    esac
done

if [[ -z "$CMD" ]]; then
    echo '{"ok":false,"error":"missing_cmd"}' >&2
    exit 78
fi

# --- audit rotate ---
if [[ -f "$AUDIT_LOG" ]]; then
    SIZE=$(stat -f%z "$AUDIT_LOG" 2>/dev/null || stat -c%s "$AUDIT_LOG" 2>/dev/null || echo 0)
    if [[ "$SIZE" -gt "$AUDIT_MAX_BYTES" ]]; then
        mv "$AUDIT_LOG" "${AUDIT_LOG}.1" 2>/dev/null || true
    fi
fi

audit() {
    local verdict="$1"
    local reason="$2"
    local ts
    ts=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    # JSON-line, escape quotes/backslashes минимально
    local cmd_esc=${CMD//\\/\\\\}
    cmd_esc=${cmd_esc//\"/\\\"}
    local reason_esc=${reason//\"/\\\"}
    printf '{"ts":"%s","verdict":"%s","reason":"%s","cmd":"%s","ppid":%d}\n' \
        "$ts" "$verdict" "$reason_esc" "$cmd_esc" "$PPID" >> "$AUDIT_LOG"
}

# ============================================================
# BLOCK list — hard-deny, неотменяемо
# ============================================================
block_check() {
    # rm -rf / variants (root, $HOME, ~)
    if [[ "$CMD" =~ rm[[:space:]]+(-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*|-[a-zA-Z]*f[a-zA-Z]*r[a-zA-Z]*)[[:space:]]+(/|/\*|\$HOME|\~|\~/) ]]; then
        echo "rm -rf / or \$HOME"
        return 0
    fi
    # Fork bomb
    if [[ "$CMD" == *':(){'* ]] || [[ "$CMD" == *':|:&'* ]]; then
        echo "fork bomb"
        return 0
    fi
    # chmod 777 /
    if [[ "$CMD" =~ chmod[[:space:]]+-?R?[[:space:]]*777[[:space:]]+/ ]]; then
        echo "chmod 777 /"
        return 0
    fi
    # sudo (любая sudo-операция)
    if [[ "$CMD" =~ (^|[[:space:];&|])sudo([[:space:]]|$) ]]; then
        echo "sudo not allowed"
        return 0
    fi
    # dd if=/dev/zero of=/dev/...
    if [[ "$CMD" =~ dd[[:space:]].*of=/dev/(disk|sd|nvme|hd) ]]; then
        echo "dd to raw device"
        return 0
    fi
    # mkfs
    if [[ "$CMD" =~ (^|[[:space:];&|])mkfs ]]; then
        echo "mkfs"
        return 0
    fi
    # > /dev/sda etc
    if [[ "$CMD" =~ \>[[:space:]]*/dev/(sd|disk|nvme|hd) ]]; then
        echo "redirect to raw device"
        return 0
    fi
    # /etc/passwd, /etc/sudoers writes
    if [[ "$CMD" =~ (\>|tee[[:space:]])[[:space:]]*/etc/(passwd|sudoers|shadow) ]]; then
        echo "/etc system file write"
        return 0
    fi
    # /etc/hosts non-localhost append (heuristic: write to /etc/hosts always confirm-needed)
    if [[ "$CMD" =~ (\>\>?|tee[[:space:]])[[:space:]]*/etc/hosts ]]; then
        echo "/etc/hosts write"
        return 0
    fi
    # curl ... | bash/sh/zsh/python  (and wget variant)
    if [[ "$CMD" =~ (curl|wget)[[:space:]].*\|[[:space:]]*(bash|sh|zsh|python|python3) ]]; then
        echo "curl-pipe-shell"
        return 0
    fi
    # ssh key writes outside ~/.ssh/krab_*
    if [[ "$CMD" =~ (\>|tee[[:space:]])[[:space:]]*[^[:space:]]*/\.ssh/(id_|authorized) ]]; then
        echo "ssh key write outside krab_*"
        return 0
    fi
    # launchctl bootout system/
    if [[ "$CMD" =~ launchctl[[:space:]]+bootout[[:space:]]+system/ ]]; then
        echo "launchctl bootout system"
        return 0
    fi
    # kill -9 1 (init)
    if [[ "$CMD" =~ kill[[:space:]]+-9[[:space:]]+1([[:space:]]|$) ]]; then
        echo "kill init"
        return 0
    fi
    # reboot/shutdown/halt/poweroff
    if [[ "$CMD" =~ (^|[[:space:];&|])(reboot|shutdown|halt|poweroff)([[:space:]]|$) ]]; then
        echo "reboot/shutdown"
        return 0
    fi
    # pkill -9 launchd
    if [[ "$CMD" =~ pkill[[:space:]]+.*launchd ]]; then
        echo "kill launchd"
        return 0
    fi
    # /System or /Library writes (except /Library/LaunchAgents)
    if [[ "$CMD" =~ (\>|tee[[:space:]]|cp[[:space:]].*|mv[[:space:]].*|rm[[:space:]].*)[[:space:]]/System/ ]]; then
        echo "/System write"
        return 0
    fi
    # /Library outside LaunchAgents
    if [[ "$CMD" =~ (\>|tee[[:space:]])[[:space:]]*/Library/(Frameworks|Extensions|PrivilegedHelperTools|StartupItems) ]]; then
        echo "/Library protected dir"
        return 0
    fi
    # defaults write для critical security domains
    if [[ "$CMD" =~ defaults[[:space:]]+write[[:space:]]+com\.apple\.(security|SoftwareUpdate|AppleSecurityUpdate) ]]; then
        echo "defaults write security"
        return 0
    fi
    # ==========================================================
    # Wave 44-T-money-safety: финансовые/банковские jail-bars
    # ==========================================================
    # Payment processors — TRANSACTIONAL endpoints only
    if [[ "$CMD" =~ (paypal\.com/(send|sendmoney|cgi-bin/webscr|checkout|donate)|venmo\.com/(payment|pay)|wise\.com/[a-z]+/transfer|revolut\.com/[a-z]+/transfer|cash\.app/\\$|zellepay\.com/[a-z]+/send|stripe\.com/v1/(charges|payment_intents|transfers)) ]]; then
        echo "money: payment processor txn URL"
        return 0
    fi
    # Bank TRANSACTIONAL endpoints
    if [[ "$CMD" =~ https?://[^[:space:]\"\']*(chase\.com|bankofamerica\.com|citibank\.com|wellsfargo\.com|hsbc\.com|barclays\.co\.uk|santander\.com|bbva\.com|caixabank\.es|sabadell\.com|ing\.com|deutsche-bank\.de)[^[:space:]\"\']*(/transfer|/pay|/wire|/sendmoney|/billpay) ]]; then
        echo "money: bank txn URL"
        return 0
    fi
    # Browser navigation to ANY financial domain — block (interactive risk)
    if [[ "$CMD" =~ (krab_browser|playwright|browser_tool|selenium|chromium|firefox|safari) ]] && \
       [[ "$CMD" =~ (paypal\.com|venmo\.com|wise\.com|revolut\.com|cash\.app|zellepay\.com|chase\.com|bankofamerica\.com|citibank\.com|wellsfargo\.com|hsbc\.com|barclays\.co\.uk|santander\.com|bbva\.com|caixabank\.es|sabadell\.com|ing\.com|deutsche-bank\.de|binance\.com|coinbase\.com|kraken\.com|bybit\.com|okx\.com) ]]; then
        echo "money: browser navigation to financial domain"
        return 0
    fi
    # Generic banking patterns
    if [[ "$CMD" =~ (\.bank\.com|/ibank|/e-banking|/online-banking|/wire-transfer|/billpay|/sendmoney|/checkout/pay) ]]; then
        echo "money: banking pattern"
        return 0
    fi
    # Transfer/pay paths in URLs (curl/wget heuristic)
    if [[ "$CMD" =~ (curl|wget|http)[^|]*[[:space:]\"\']+https?://[^[:space:]\"\']*/(transfer|pay|checkout|wire)([/?\"\'[:space:]]|$) ]]; then
        echo "money: /transfer or /pay URL"
        return 0
    fi
    # Crypto exchange WRITE/order operations (read-only allowed, see below)
    if [[ "$CMD" =~ (binance\.com/api/[^[:space:]]*order|coinbase\.com/(buy|sell|send)|kraken\.com/[^[:space:]]*(AddOrder|Withdraw)|bybit\.com/[^[:space:]]*order|ftx\.com|okx\.com/api/v5/trade/order|bitstamp\.net/api/v2/buy|kucoin\.com/api/v[0-9]+/orders) ]]; then
        echo "money: crypto exchange write op"
        return 0
    fi
    # Government/tax payment endpoints
    if [[ "$CMD" =~ (irs\.gov/[^[:space:]]*payment|gov\.uk/pay|agenciatributaria\.es/[^[:space:]]*pago|/tax-return-submit) ]]; then
        echo "money: gov/tax payment"
        return 0
    fi
    # Money keywords (English + Russian) — semantic guard against
    # "send 100 USD to ...", "transfer 5 ETH to ...", "buy X for Y"
    shopt -s nocasematch
    if [[ "$CMD" =~ (send|transfer|wire|переведи|отправь|купи|оплати)[[:space:]]+[0-9]+([,.][0-9]+)?[[:space:]]*(usd|eur|gbp|rub|usdt|usdc|btc|eth|sol|\$|€|£|₽) ]]; then
        shopt -u nocasematch
        echo "money: send-N-currency keyword pattern"
        return 0
    fi
    shopt -u nocasematch
    return 1
}

# ============================================================
# CONFIRM list — требует owner-token
# ============================================================
confirm_check() {
    # rm -rf <path> с глубоким wildcard или большим scope (heuristic)
    if [[ "$CMD" =~ rm[[:space:]]+-[a-zA-Z]*r[a-zA-Z]*f.*\* ]]; then
        echo "mass-delete with wildcard"
        return 0
    fi
    # git push --force, --force-with-lease
    if [[ "$CMD" =~ git[[:space:]]+push.*(--force|-f([[:space:]]|$)) ]]; then
        echo "git push --force"
        return 0
    fi
    # pip install outside venv (heuristic: no `venv/bin/` prefix)
    if [[ "$CMD" =~ (^|[[:space:];&|])pip[[:space:]]+install ]] && [[ ! "$CMD" =~ venv/bin/pip ]]; then
        echo "pip install outside venv"
        return 0
    fi
    # brew uninstall, npm install -g
    if [[ "$CMD" =~ brew[[:space:]]+uninstall ]]; then
        echo "brew uninstall"
        return 0
    fi
    if [[ "$CMD" =~ npm[[:space:]]+install[[:space:]]+-g ]]; then
        echo "npm install -g"
        return 0
    fi
    # security add-generic-password (credentials write)
    if [[ "$CMD" =~ security[[:space:]]+add-generic-password ]]; then
        echo "security add-generic-password"
        return 0
    fi
    # ==========================================================
    # Wave 44-T-money-safety: SOFT confirm для денежных keywords
    # ==========================================================
    # buy / purchase / order / subscribe / checkout (без явной валюты)
    if [[ "$CMD" =~ (^|[[:space:]\"\'])(buy|purchase|subscribe[[:space:]]+to|order[[:space:]]+now|checkout)([[:space:]\"\']|$) ]]; then
        echo "money: purchase/subscribe keyword (soft confirm)"
        return 0
    fi
    # READ-ONLY access к финансовым доменам — только при наличии token
    # (paypal.com/dashboard, my-bank.com и т.п. без /transfer-pattern)
    if [[ "$CMD" =~ https?://[^[:space:]\"\']*(paypal\.com|chase\.com|bankofamerica\.com|revolut\.com|wise\.com|stripe\.com) ]]; then
        echo "money: financial domain read access"
        return 0
    fi
    return 1
}

verify_owner_token() {
    [[ -z "$OWNER_TOKEN" ]] && return 1
    [[ ! -f "$TOKEN_PATH" ]] && return 1
    local stored
    stored=$(cat "$TOKEN_PATH" 2>/dev/null | tr -d '[:space:]')
    [[ -z "$stored" ]] && return 1
    [[ "$OWNER_TOKEN" == "$stored" ]]
}

# --- main flow ---
if reason=$(block_check); then
    audit "BLOCK" "$reason"
    echo "{\"ok\":false,\"verdict\":\"BLOCK\",\"reason\":\"$reason\"}" >&2
    exit 78
fi

if reason=$(confirm_check); then
    if verify_owner_token; then
        audit "CONFIRM_OK" "$reason"
    else
        audit "NEEDS_CONFIRM" "$reason"
        echo "{\"ok\":false,\"verdict\":\"NEEDS_OWNER_CONFIRM\",\"reason\":\"$reason\"}" >&2
        exit 79
    fi
fi

audit "ALLOW" "exec"
# Execute the command via bash -c, preserving exit code
bash -c "$CMD"
exit $?
