#!/bin/sh
# watch_session.sh MINUTES LOG: while the user plays, log once a minute the app's process id, its latest frame-timing line and its count of [RED] lines.
# It only reads (no input, no restart), so it can run during someone's play session; a changed pid or "DEAD" means the app restarted or crashed.
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
eval "$(python "$ROOT/devtools/device.py" --sh)"
[ $# -eq 2 ] || { echo "usage: watch_session.sh MINUTES LOG" >&2; exit 2; }
end=$(( $(date +%s) + $1 * 60 )); : > "$2"
while [ "$(date +%s)" -lt "$end" ]; do
  p=$(MSYS_NO_PATHCONV=1 $ADB shell "pidof $PKG" 2>/dev/null | tr -d '\r')
  t=$(MSYS_NO_PATHCONV=1 $ADB shell "grep -a 'timing up to' $F/log.txt | tail -1 | cut -c1-140; grep -ac RED $F/log.txt" 2>/dev/null | tr '\r\n' '  ')
  echo "$(date +%H:%M:%S) pid=${p:-DEAD} $t" >> "$2"
  sleep 60
done
echo done >> "$2"; awk '{print $2}' "$2" | sort | uniq -c
