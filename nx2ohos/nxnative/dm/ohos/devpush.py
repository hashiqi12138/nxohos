# Fast debug cycle for the OHOS port: build the host lib, re-pack entry only (small: lib + mods + manifest), install
# entry alone over the installed bundle (the bulk data modules stay installed, the Android OBB / iOS Documents split),
# start, and tail the dmhost log. One round is a few minutes, none of it spent moving the 13 GB bulk again.
#   python ohos/devpush.py --game <title ID> [--mods <dir>] [--lib <file>] [--log-lines N] [--no-start] [--keep]
# Prereq (once per game): a full bundle install (mkhap.py --data ...) so data1..N sit on the device.
# The entry it builds carries dm.files.tsv with the SAME stamp as that full install: staging then copies nothing.
import argparse, os, shutil, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))          # dm/ohos/
DM = os.path.dirname(HERE)
BUNDLE = 'dev.eden_emu.ohos'
ABILITY = 'EntryAbility'
REMOTE_DIR = '/data/local/tmp/nx2a-install'

ap = argparse.ArgumentParser()
ap.add_argument('--game', required=True)
ap.add_argument('--lib', help='libdmhost.so to pack (default: build it first with build_ohos.py)')
ap.add_argument('--mods', help='mods dir (default: keep the one already in the build tree)')
ap.add_argument('--no-build', action='store_true', help='do not build the lib first')
ap.add_argument('--no-start', action='store_true')
ap.add_argument('--keep', action='store_true', help='keep the ASCII build dir (debug the hvigor project)')
ap.add_argument('--version-code', type=int, default=0, help='override the auto versionCode (seconds)')
a = ap.parse_args()

def find_deveco():
    for p in (os.environ.get('DEVECO_HOME'), r'E:\Program Files\Huawei\DevEco Studio',
              r'D:\dev-echo\DevEco Studio', r'C:\Program Files\Huawei\DevEco Studio'):
        if p and os.path.isdir(os.path.join(p, 'tools', 'hvigor', 'bin')): return p
    return None
DEVECO = find_deveco()
if not DEVECO: sys.exit('devpush: DevEco Studio not found')
HDC = os.path.join(DEVECO, 'sdk', 'default', 'openharmony', 'toolchains', 'hdc.exe')
HVIGOR = os.path.join(DEVECO, 'tools', 'hvigor', 'bin', 'hvigorw.bat')

def run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, errors='replace', **kw)
    return r.returncode, (r.stdout or '') + (r.stderr or '')

def hdc(*args, timeout=600):
    return run([HDC, *args], timeout=timeout)

# 1. build the lib (unless told not to)
lib = a.lib
if not lib:
    if not a.no_build:
        t0 = time.time()
        rc, out = run([sys.executable, os.path.join(HERE, 'build_ohos.py'), '--game', a.game, '--lib'], timeout=1800)
        if rc: sys.exit('devpush: build_ohos failed:\n' + out[-800:])
        print('lib built in %.0f s' % (time.time() - t0))
    lib = os.path.join(DM, 'ohos', 'out', a.game, 'libdmhost.so')
if not os.path.isfile(lib): sys.exit('devpush: %s is missing' % lib)

# 2. the ASCII build dir: reuse the full install's tree (it holds the placed bulk modules and the manifest)
build = os.path.join('F:', os.sep, 'ohos_build_dmhost_' + a.game)
if not os.path.isdir(build): sys.exit('devpush: %s not found - do a full mkhap.py --data install once first' % build)

# 3. refresh lib and (optionally) mods in the tree
libdir = os.path.join(build, 'entry', 'libs', 'arm64-v8a')
os.makedirs(libdir, exist_ok=True)
shutil.copy2(lib, os.path.join(libdir, 'libdmhost.so'))
if a.mods:
    dst = os.path.join(build, 'entry', 'src', 'main', 'resources', 'rawfile', 'dm', 'mods')
    if os.path.isdir(dst): shutil.rmtree(dst)
    shutil.copytree(a.mods, dst)
    # a changed mods set changes the stamp only through the manifest; keep the manifest's stamp line untouched so staging
    # skips (the mods' file list and sizes in the manifest must match, so rewrite those columns only)
    man = os.path.join(build, 'entry', 'src', 'main', 'resources', 'rawfile', 'dm.files.tsv')
    lines = open(man, encoding='utf-8').read().splitlines()
    out, mods_rels = [], {r: os.path.getsize(os.path.join(a.mods, r)) for r in
                          sorted(os.path.relpath(os.path.join(d, f), a.mods).replace('\\', '/') for d, _, fs_ in os.walk(a.mods) for f in fs_)}
    for ln in lines:
        parts = ln.split('\t')
        if len(parts) == 3 and parts[0].startswith('mods/'):
            rel = parts[0][len('mods/'):]
            if rel in mods_rels: parts[1] = str(mods_rels[rel])
            ln = '\t'.join(parts)
        out.append(ln)
    open(man, 'w', encoding='utf-8', newline='\n').write('\n'.join(out) + '\n')
    print('mods refreshed: %d files' % len(mods_rels))

# 4. versionCode: auto (seconds since the epoch, always above any installed build)
app_json = os.path.join(build, 'AppScope', 'app.json5')
import re
vc = a.version_code or int(time.time())
txt = open(app_json, encoding='utf-8').read()
open(app_json, 'w', encoding='utf-8').write(re.sub(r'"versionCode"\s*:\s*\d+', '"versionCode": %d' % vc, txt, count=1))
print('versionCode: %d' % vc)

# 5. hvigor: entry module only
env = dict(os.environ); env['DEVECO_SDK_HOME'] = os.path.join(DEVECO, 'sdk')
env['PATH'] = os.path.join(DEVECO, 'tools', 'node') + os.pathsep + env.get('PATH', '')
jbr = os.path.join(DEVECO, 'jbr')
if os.path.isdir(jbr): env['JAVA_HOME'] = jbr; env['PATH'] = os.path.join(jbr, 'bin') + os.pathsep + env['PATH']
t0 = time.time()
rc, out = run([HVIGOR, '--mode', 'module', '-p', 'module=entry@default', '-p', 'product=default',
               '-p', 'buildMode=debug', 'assembleHap', '--no-daemon'], cwd=build, env=env, timeout=1800)
if rc: sys.exit('devpush: hvigor failed:\n' + out[-800:])
print('entry packed in %.0f s' % (time.time() - t0))

signed = os.path.join(build, 'entry', 'build', 'default', 'outputs', 'default', 'entry-default-signed.hap')
print('entry: %.1f MB' % (os.path.getsize(signed) / 2**20))

# 6. push and install entry alone
rc, out = hdc('list', 'targets', timeout=60)
if rc or 'Empty' in out: sys.exit('devpush: no device on hdc')
rc, out = hdc('shell', 'rm -rf %s/nx2a-entry; mkdir -p %s/nx2a-entry' % (REMOTE_DIR, REMOTE_DIR), timeout=120)
t0 = time.time()
rc, out = hdc('file', 'send', signed, '%s/nx2a-entry/entry-default-signed.hap' % REMOTE_DIR, timeout=1800)
if rc or 'finish' not in out.lower(): sys.exit('devpush: send failed: ' + out.strip()[:200])
print('sent in %.0f s' % (time.time() - t0))
rc, out = hdc('shell', 'bm', 'install', '-p', '%s/nx2a-entry' % REMOTE_DIR, '-r', '-w', '600', timeout=900)
print('install:', out.strip()[:160])
if 'successfully' not in out.lower(): sys.exit('devpush: install failed')

# 7. start and tail
if not a.no_start:
    hdc('shell', 'hilog', '-r', timeout=60)
    rc, out = hdc('shell', 'aa', 'start', '-b', BUNDLE, '-a', ABILITY, timeout=120)
    print('start:', out.strip())
    time.sleep(25)
    rc, out = hdc('shell', 'hilog', '-x', '-T', 'dmhost', timeout=120)
    print(out[-4000:])

# 8. keep or clean the remote staging dir
if not a.keep:
    hdc('shell', 'rm -rf %s/nx2a-entry' % REMOTE_DIR, timeout=120)
print('DEVPUSH DONE')
