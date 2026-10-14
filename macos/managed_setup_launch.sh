#!/bin/sh
# Keep bootstrap downloads tied to the app, including before Python is installed.
set -u
PARENT_PID="$1"
shift
case "$PARENT_PID" in ''|*[!0-9]*) exit 2 ;; esac
if [ "$PARENT_PID" -le 1 ]; then exit 2; fi
"$@" &
SETUP_PID=$!
stop_setup() {
    kill -TERM "$SETUP_PID" 2>/dev/null || true
    wait "$SETUP_PID" 2>/dev/null || true
}
trap 'stop_setup; exit 130' INT TERM
while kill -0 "$SETUP_PID" 2>/dev/null; do
    if ! kill -0 "$PARENT_PID" 2>/dev/null; then
        stop_setup
        exit 130
    fi
    sleep 1
done
wait "$SETUP_PID"
SETUP_EXIT_STATUS=$?
trap - INT TERM
exit "$SETUP_EXIT_STATUS"
