#!/usr/bin/env bash
#
# stop-all.sh — stop everything launched by start-all.sh.
#
# Reads scripts/.pids and terminates each recorded process (and its children).
# Safe to run multiple times; missing PIDs are reported, not errored.
#
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$SCRIPT_DIR/.pids"

step() { printf '\033[36m==> %s\033[0m\n' "$1"; }
ok()   { printf '\033[32m  OK  %s\033[0m\n' "$1"; }
bad()  { printf '\033[33m  !!  %s\033[0m\n' "$1"; }

if [[ ! -f "$PID_FILE" ]]; then
    bad "No .pids file at $PID_FILE - nothing to stop."
    exit 0
fi

while read -r pid; do
    [[ "$pid" =~ ^[0-9]+$ ]] || continue
    step "Stopping PID $pid (and children)"
    # Kill the whole process group; fall back to the bare PID.
    if kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null; then
        ok "PID $pid signalled"
    else
        bad "PID $pid not found (already stopped?)"
    fi
done < "$PID_FILE"

# Give them a moment, then force-kill any survivors.
sleep 2
while read -r pid; do
    [[ "$pid" =~ ^[0-9]+$ ]] || continue
    if kill -0 "$pid" 2>/dev/null; then
        kill -KILL "$pid" 2>/dev/null && bad "PID $pid force-killed"
    fi
done < "$PID_FILE"

rm -f "$PID_FILE"
echo
ok "All recorded services have been signalled."
