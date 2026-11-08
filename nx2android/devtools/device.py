# The device and app the dev tools work with, instead of a serial and package in every script:
#   devtools/device.json (not committed) {"serial": "<adb serial, e.g. 192.168.1.20:5555>", "package": "<the app's package>"}, or NX2A_DEVICE / NX2A_PACKAGE (these win).
#   No serial: the only connected device. No package: the profile's package for NX2A_GAME (a title ID); with none of these the tools stop.
# From Python: from device import ADB, PKG, FILES (ADB = the adb command with -s). From a shell script: eval "$(python devtools/device.py --sh)" sets ADB, SERIAL, PKG, F.
import json, os, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from porter.toolchain import find_adb   # noqa: E402


def _conf():
    try: return json.loads((HERE / 'device.json').read_text(encoding='utf-8'))
    except (OSError, ValueError): return {}


def _package(c):
    if os.environ.get('NX2A_PACKAGE') or c.get('package'): return os.environ.get('NX2A_PACKAGE') or c['package']
    if os.environ.get('NX2A_GAME'):
        from porter import profiles
        return profiles.load(os.environ['NX2A_GAME']).package
    sys.exit('devtools: no app to work with: set "package" in devtools/device.json, NX2A_PACKAGE, or NX2A_GAME (a title ID)')


_c = _conf()
SERIAL = os.environ.get('NX2A_DEVICE') or _c.get('serial') or ''
ADB_EXE = str(find_adb() or 'adb')
ADB = [ADB_EXE] + (['-s', SERIAL] if SERIAL else [])
PKG = _package(_c)
FILES = f'/sdcard/Android/data/{PKG}/files'   # the app's files folder: log.txt, env.txt, ctl.txt, the data pack

if __name__ == '__main__':
    if sys.argv[1:] == ['--sh']:
        q = lambda s: "'" + s.replace("'", "'\\''") + "'"
        adb = ADB_EXE.replace('\\', '/')
        print(f'ADB={q(adb + (" -s " + SERIAL if SERIAL else ""))}; SERIAL={q(SERIAL)}; PKG={q(PKG)}; F={q(FILES)}')
    else:
        print(f'adb {ADB_EXE}\nserial {SERIAL or "(the only connected device)"}\npackage {PKG}\nfiles {FILES}')
