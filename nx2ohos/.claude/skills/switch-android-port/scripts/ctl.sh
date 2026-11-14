#!/bin/sh
# ctl.sh LINE...: append lines to the running app's ctl.txt in one adb call, so they arrive together (e.g. ctl.sh 'X' or ctl.sh 'B~0.15' 'env DM_VK_SHOTS=8').
# Needs a development build (porter port --debug) with DM_CTL=<files>/ctl.txt in env.txt; release builds ignore ctl.txt. Lines must not contain single quotes or %.
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
eval "$(python "$ROOT/devtools/device.py" --sh)"   # ADB, PKG, F from devtools/device.json
[ $# -gt 0 ] || { echo "usage: ctl.sh LINE..." >&2; exit 2; }
body=""; for l in "$@"; do body="$body$l\\n"; done
MSYS_NO_PATHCONV=1 $ADB shell "printf '$body' >> $F/ctl.txt"
