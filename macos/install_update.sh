#!/bin/zsh
# Called only with a verified, prepared bundle after user approval.
set -eu
APP_PID="$1"
TARGET="$2"
STAGED="$3"
LOG_FILE="$4"
BACKUP="${TARGET}.previous.$$.app"
exec >> "$LOG_FILE" 2>&1
echo "Waiting for DubCanvas to quit…"
for attempt in {1..60}; do
  if ! kill -0 "$APP_PID" 2>/dev/null; then break; fi
  if [[ "$attempt" -eq 15 ]]; then
    # Older builds can remain in a modal UI or a blocked backend shutdown.
    # Never signal a reused PID or another program: match the installed binary.
    RUNNING_COMMAND="$(/bin/ps -p "$APP_PID" -o comm= 2>/dev/null || true)"
    if [[ "$RUNNING_COMMAND" == "$TARGET/Contents/MacOS/DubCanvas" ]]; then
      echo "Graceful quit timed out; sending TERM to the verified DubCanvas process $APP_PID."
      kill -TERM "$APP_PID" 2>/dev/null || true
    else
      echo "Quit fallback skipped: PID $APP_PID does not match the installed DubCanvas executable."
    fi
  fi
  sleep 1
done
if kill -0 "$APP_PID" 2>/dev/null; then
  echo "Update aborted: app is still running."
  /usr/bin/open -R "$LOG_FILE"
  exit 1
fi
MOVED_OLD=0
MOVED_NEW=0
rollback() {
  trap - ZERR
  set +e
  if [[ "$MOVED_NEW" -eq 1 ]]; then /bin/mv "$TARGET" "$STAGED"; fi
  if [[ "$MOVED_OLD" -eq 1 ]]; then /bin/mv "$BACKUP" "$TARGET"; fi
  echo "Update failed; restored previous app."
  /usr/bin/open "$TARGET" || true
  /usr/bin/open -R "$LOG_FILE" || true
  exit 1
}
trap rollback ZERR
/bin/mv "$TARGET" "$BACKUP"
MOVED_OLD=1
/bin/mv "$STAGED" "$TARGET"
MOVED_NEW=1
/usr/bin/open "$TARGET"
trap - ZERR
echo "Update installed and reopened."
/bin/rm -rf "$BACKUP"
# The OS owns temporary update cleanup. Never delete the helper's parent directory.
