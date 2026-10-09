#!/usr/bin/env bash

ENVFILE="/etc/vargamesh/mining.env"
BRIDGE="/opt/vargamesh/mining/bin/vargamesh-auxpow-bridge.py"
DEFAULT_PASSFILE="CHANGE_ME"

fail()
{
    echo "VargaMesh bridge startup refused: $1" >&2
    exit "${2:-1}"
}

[ -e /etc/vargamesh/MAINNET-LAUNCH-APPROVED ] ||
    fail "MAINNET-LAUNCH-APPROVED absent"

[ -r "$ENVFILE" ] ||
    fail "$ENVFILE missing/unreadable"

# shellcheck disable=SC1090
. "$ENVFILE"

[ -n "${VMESH_RPC_URL:-}" ] ||
    fail "VMESH_RPC_URL empty"

[ -n "${VMESH_RPC_USER:-}" ] ||
    fail "VMESH_RPC_USER empty"

[ -n "${VMESH_DEVELOPER_PAYOUT_SCRIPT:-}" ] ||
    fail "VMESH_DEVELOPER_PAYOUT_SCRIPT empty"

[ "${VMESH_DEVELOPER_PAYOUT_SCRIPT,,}" = "001482608979e16b0fa9a38cc984c827fbc5a62afc70" ] ||
    fail "VMESH_DEVELOPER_PAYOUT_SCRIPT must match fixed 1% VMESH development address"

[ -n "${VMESH_PARENT_SCRIPT:-}" ] ||
    fail "VMESH_PARENT_SCRIPT empty"

[ "${VMESH_PARENT_SCRIPT}" != "CHANGE_ME" ] ||
    fail "VMESH_PARENT_SCRIPT still CHANGE_ME"

case "${VMESH_DEVELOPER_PAYOUT_SCRIPT}" in
    *[!0-9a-fA-F]*)
        fail "VMESH_DEVELOPER_PAYOUT_SCRIPT not hex"
        ;;
esac

case "${VMESH_PARENT_SCRIPT}" in
    *[!0-9a-fA-F]*)
        fail "VMESH_PARENT_SCRIPT not hex"
        ;;
esac

LISTEN="${VMESH_BRIDGE_LISTEN:-127.0.0.1}"
PORT="${VMESH_BRIDGE_PORT:-3932}"

[ "$LISTEN" = "127.0.0.1" ] ||
    fail "Bridge must remain loopback-only"

PASSFILE="${VMESH_RPC_PASS_FILE:-$DEFAULT_PASSFILE}"

[ -r "$PASSFILE" ] ||
    fail "RPC pass file missing/unreadable"

#
# Defensive cleanup in case an old environment still contains it.
#
unset VMESH_RPC_PASS

exec /usr/bin/python3 "$BRIDGE" \
    --listen "$LISTEN" \
    --port "$PORT" \
    --vmesh-url "$VMESH_RPC_URL" \
    --vmesh-user "$VMESH_RPC_USER" \
    --vmesh-pass-file "$PASSFILE" \
    --developer-payout-script "$VMESH_DEVELOPER_PAYOUT_SCRIPT" \
    --parent-script "$VMESH_PARENT_SCRIPT"
