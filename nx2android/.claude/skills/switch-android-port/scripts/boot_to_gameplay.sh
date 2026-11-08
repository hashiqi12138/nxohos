#!/bin/sh
# boot_to_gameplay.sh DETECTOR.py: restart the app and press A only while the screen is not gameplay yet, until DETECTOR says gameplay twice in a row.
# DETECTOR.py defines is_gameplay(rgb) -> bool for a screenshot as a numpy array (see detect_neo.py). A press goes to ctl.txt ("A~0.2", development builds);
# set BOOT_TAP="x y" to hold a touch there instead (release builds, which ignore ctl.txt: the on-screen A button). Exits 1 if gameplay never shows.
# A blind press in gameplay acts on the user's save (it advanced NEO's story): this script presses only while the detector says "not yet".
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"; HERE="$(cd "$(dirname "$0")" && pwd)"
eval "$(python "$ROOT/devtools/device.py" --sh)"
[ -f "$1" ] || { echo "usage: boot_to_gameplay.sh DETECTOR.py" >&2; exit 2; }
DET="$(cd "$(dirname "$1")" && { pwd -W 2>/dev/null || pwd; })/$(basename "$1")"   # pwd -W: a Windows path for Windows Python
SHOT="$(python -c 'import os, tempfile; print(os.path.join(tempfile.gettempdir(), "boot_shot.png").replace(os.sep, "/"))')"   # a path both the shell and Python read
MSYS_NO_PATHCONV=1 $ADB shell "am force-stop $PKG; am start -n $PKG/android.app.NativeActivity" >/dev/null 2>&1
sleep 30
ok=0
for i in $(seq 1 15); do
  MSYS_NO_PATHCONV=1 $ADB exec-out screencap -p > "$SHOT"
  s=$(python -c "
import importlib.util, numpy as np
from PIL import Image
spec = importlib.util.spec_from_file_location('det', r'$DET'); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
print('game' if m.is_gameplay(np.asarray(Image.open(r'$SHOT').convert('RGB'))) else 'menu')")
  if [ "$s" = game ]; then ok=$((ok + 1)); [ $ok -ge 2 ] && { echo "in gameplay after $i checks"; exit 0; }; sleep 5; continue; fi
  ok=0
  if [ -n "$BOOT_TAP" ]; then MSYS_NO_PATHCONV=1 $ADB shell "input swipe $BOOT_TAP $BOOT_TAP 150"; else sh "$HERE/ctl.sh" 'A~0.2'; fi
  sleep 6
done
echo "never reached gameplay"; exit 1
