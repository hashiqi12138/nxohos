#!/bin/sh
# Soak test of the running app: moves the stick and camera (never presses A, so a save's story does not advance) for N minutes, logging the app's pid
# and its latest frame timing every minute.   soak.sh <minutes> <out log>
eval "$(python "$(dirname "$0")/device.py" --sh)"; A=$ADB   # devtools/device.json: the device and the app
end=$(( $(date +%s) + $1 * 60 )); next=0; moves="LUp~0.8 LRight~0.6 LDown~0.8 LLeft~0.6 RRight~0.3 LUp~0.5 RLeft~0.3"
while [ "$(date +%s)" -lt "$end" ]; do
  for m in $moves; do MSYS_NO_PATHCONV=1 $A shell "echo '$m' >> $F/ctl.txt" >/dev/null 2>&1; sleep 3; done
  if [ "$(date +%s)" -ge "$next" ]; then next=$(( $(date +%s) + 60 ))
    p=$(MSYS_NO_PATHCONV=1 $A shell "pidof $PKG" 2>/dev/null | tr -d '\r')
    t=$(MSYS_NO_PATHCONV=1 $A shell "grep -a 'timing up to' $F/log.txt | tail -1" 2>/dev/null | cut -c1-140)
    echo "$(date +%H:%M:%S) pid=${p:-DEAD} $t" >> "$2"; [ -z "$p" ] && { echo "app gone" >> "$2"; exit 1; }
  fi
done
echo "soak done" >> "$2"
