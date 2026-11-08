#!/bin/sh
# frame_shots.sh N OUTDIR [LINE...]: send the ctl lines and "env DM_VK_SHOTS=N" in one write, wait for the N presented frames, pull them into OUTDIR.
# The frames are what the game presented (after its GPU work finished), so a glitch in them is in the game's output, not in presentation.
# Example, NEO's menu-to-field wipe: ctl.sh X; sleep 3.5; frame_shots.sh 8 shots 'B~0.15'
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
eval "$(python "$ROOT/devtools/device.py" --sh)"
[ $# -ge 2 ] || { echo "usage: frame_shots.sh N OUTDIR [LINE...]" >&2; exit 2; }
n=$1; out=$2; shift 2
MSYS_NO_PATHCONV=1 $ADB shell "rm -f $F/frame_*.ppm"
sh "$(dirname "$0")/ctl.sh" "$@" "env DM_VK_SHOTS=$n"
i=0; while [ $i -lt 90 ]; do
  c=$(MSYS_NO_PATHCONV=1 $ADB shell "ls $F | grep -c '^frame_'" | tr -d '\r')
  [ "${c:-0}" -ge "$n" ] && break; sleep 1; i=$((i+1))
done
sleep 2   # the last file may still be being written
mkdir -p "$out"
for p in $(MSYS_NO_PATHCONV=1 $ADB shell "ls $F | grep '^frame_'" | tr -d '\r'); do MSYS_NO_PATHCONV=1 $ADB pull "$F/$p" "$out/" >/dev/null 2>&1; done
MSYS_NO_PATHCONV=1 $ADB shell "grep -a 'frame shots' $F/log.txt | tail -1"
echo "$(ls "$out" | grep -c '^frame_') frames in $out"
