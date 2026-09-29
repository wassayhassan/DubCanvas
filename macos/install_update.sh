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
