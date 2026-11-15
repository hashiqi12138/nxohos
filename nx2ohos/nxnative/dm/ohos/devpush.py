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
ap.add_argument('--data-dir', dest='data_dir',
                help='serve this porter data pack over a loopback http channel (hdc fport) so the app imports it '
                     'into its sandbox itself: one import, then the stamp matches and rounds ship the entry alone')
ap.add_argument('--import-wait', dest='import_wait', type=int, default=3600,
                help='seconds to keep the pack server up and watch the import (default: %(default)s)')
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

# 2b. --data-dir: serve the pack over loopback for the app's own import (P2.1). Neither the shell domain nor the
# app can cross the SELinux boundary (tmp is data_local_tmp, the sandbox is debug_hap_data_file), so the pack
# travels over http: this process serves it, `hdc fport tcp:PORT tcp:PORT` forwards the device's loopback to it,
# and the app pulls what it does not have into its sandbox. Once the stamp matches, nothing is pulled again.
IMPORT_PORT = 18899
def start_pack_server(pack, manifest):
    import http.server, socketserver, threading
    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=pack, **kw)
        def do_GET(self):
            if self.path == '/ping':
                body = b'PONG'
                self.send_response(200); self.send_header('Content-Length', str(len(body))); self.end_headers()
                self.wfile.write(body); return
            if self.path == '/dm.files.tsv':
                with open(manifest, 'rb') as f: body = f.read()
                self.send_response(200); self.send_header('Content-Length', str(len(body))); self.end_headers()
                self.wfile.write(body); return
            super().do_GET()
        def log_message(self, *a): pass
    srv = socketserver.ThreadingTCPServer(('127.0.0.1', IMPORT_PORT), Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv

if a.data_dir:
    rc, out = hdc('list', 'targets', timeout=60)
    if rc or 'Empty' in out: sys.exit('devpush: no device on hdc')
    pack = os.path.abspath(a.data_dir)
    if not os.path.isfile(os.path.join(pack, 'nx2a-data.json')):
        sys.exit('devpush: %s has no nx2a-data.json - point --data-dir at the porter data pack folder' % pack)
    # The manifest is mkhap's product (a stamp over rel/size/mtime of the pack). The build tree's copy may be
    # gone (a slim entry carries none), so take it from wherever it survives - the build tree first, then a
    # previously staged dmdata dir - and otherwise generate it here from the pack itself.
    man = os.path.join(build, 'entry', 'src', 'main', 'resources', 'rawfile', 'dm.files.tsv')
    if not os.path.isfile(man):
        man = os.path.join(build, 'dm.files.tsv')
    if not os.path.isfile(man):
        import hashlib
        files = sorted(os.path.relpath(os.path.join(d, f), pack).replace('\\', '/')
                       for d, _, fs_ in os.walk(pack) for f in fs_ if f != 'dm.files.tsv')
        h = hashlib.sha1()
        for rel in files:
            st = os.stat(os.path.join(pack, rel))
            h.update(('%s\t%d\t%d\n' % (rel, st.st_size, st.st_mtime_ns)).encode())
        man = os.path.join(build, 'dm.files.tsv')
        with open(man, 'w', encoding='utf-8', newline='\n') as f:
            f.write('# DM_DATA %s\n' % h.hexdigest())
            for rel in files:
                f.write('%s\t%d\tentry\n' % (rel, os.path.getsize(os.path.join(pack, rel))))
        print('manifest generated from the pack: %d files' % len(files))
    srv = start_pack_server(pack, man)
    print('pack server on 127.0.0.1:%d (the app pulls from it on start)' % IMPORT_PORT)
    rc, out = hdc('fport', 'rm', 'tcp:%d' % IMPORT_PORT, 'tcp:%d' % IMPORT_PORT, timeout=60)
    rc, out = hdc('fport', 'tcp:%d' % IMPORT_PORT, 'tcp:%d' % IMPORT_PORT, timeout=60)
    print('fport:', out.strip()[:120])
    globals()['_pack_server'] = srv   # kept alive until the process ends

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

# 4. versionCode: above whatever the device already holds (bm dump), and above any clock guess - the bundle's
# history includes 2,000,000,002 (the imposter-app war), which a time-based code can never beat
def run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, errors='replace', **kw)
    return r.returncode, (r.stdout or '') + (r.stderr or '')

def hdc(*args, timeout=600):
    return run([HDC, *args], timeout=timeout)

app_json = os.path.join(build, 'AppScope', 'app.json5')
import re
vc = a.version_code or int(time.time())
try:
    rc, out = hdc('shell', 'bm', 'dump', '-n', BUNDLE, timeout=60)
    codes = [int(x) for x in re.findall(r'"versionCode"\s*:\s*(\d+)', out)]
    if codes and max(codes) >= vc: vc = max(codes) + 1   # the appQuickFix block carries its own versionCode fields; the top-level code is the max
except Exception:
    pass   # offline or not installed: the clock value stands
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

# 6. push and install: entry alone. A slim entry carries no data and does not read the data modules - the app
# imports the pack from /data/local/tmp/dmdata/<title>/ instead (P2.1). With the old data-carrying entry the
# install set had to hold all six modules (bm drops those missing from the folder, and the next staging's
# createModuleContext('dataN') then fails with "Invalid input parameter"); that constraint is gone here.
rc, out = hdc('list', 'targets', timeout=60)
if rc or 'Empty' in out: sys.exit('devpush: no device on hdc')
# the app must find its pack somewhere: the tmp import dir, or the rawfile modules of a data-carrying entry
rc, out = hdc('shell', 'ls /data/local/tmp/dmdata/%s/dm.files.tsv 2>/dev/null && echo HAVE_IMPORT' % a.game, timeout=60)
have_import = 'HAVE_IMPORT' in out
rc, out = hdc('shell', 'ls %s/nx2a-install/data5-default-signed.hap 2>/dev/null && echo HAVE_MODULES' % REMOTE_DIR, timeout=60)
have_modules = 'HAVE_MODULES' in out
if not have_import and not have_modules:
    sys.exit('devpush: no data on the device - push a pack (--data-dir) or run a full mkhap --data install once')
hdc('shell', 'rm -rf %s/nx2a-entry; mkdir -p %s/nx2a-entry' % (REMOTE_DIR, REMOTE_DIR), timeout=120)
if have_modules and not have_import:
    # the old shape: the entry's manifest names data1..N, so the whole set must go up together
    hdc('shell', 'for f in %s/nx2a-install/data*-default-signed.hap; do cp $f %s/nx2a-entry/ 2>/dev/null; done' % (REMOTE_DIR, REMOTE_DIR), timeout=300)
    print('install set: entry + the six modules (rawfile pack)')
else:
    print('install set: entry alone (the pack imports over the loopback channel)')
t0 = time.time()
rc, out = hdc('file', 'send', signed, '%s/nx2a-entry/entry-default-signed.hap' % REMOTE_DIR, timeout=1800)
if rc or 'finish' not in out.lower(): sys.exit('devpush: send failed: ' + out.strip()[:200])
print('sent in %.0f s' % (time.time() - t0))
rc, out = hdc('shell', 'bm', 'install', '-p', '%s/nx2a-entry' % REMOTE_DIR, '-r', '-w', '600', timeout=900)
print('install:', out.strip()[:160])
if 'successfully' not in out.lower(): sys.exit('devpush: install failed')

# 7. start and tail. The pack server must outlive the app's import: keep it serving and watch the staging
# progress until the import ends (or --import-wait runs out). A first 14 GB import takes a while; a later round
# sees the stamp match and starts the game in seconds.
if not a.no_start:
    hdc('shell', 'hilog', '-r', timeout=60)
    rc, out = hdc('shell', 'aa', 'start', '-b', BUNDLE, '-a', ABILITY, timeout=120)
    print('start:', out.strip())
    t0 = time.time(); last_n = -1
    while time.time() - t0 < a.import_wait:
        time.sleep(15)
        rc, out = hdc('shell', 'hilog', '-x', '-T', 'dmhost', timeout=120)
        staged = [int(x) for x in re.findall(r'staged (\d+) files', out)]
        cur = max(staged) if staged else 0
        if cur != last_n:
            print('  staged %d files (%.0f s)' % (cur, time.time() - t0)); last_n = cur
        if 'starting the game' in out: print('  game started'); break
        if 'staging failed' in out or 'carries no data pack' in out: print('  staging did not import'); break
    rc, out = hdc('shell', 'hilog', '-x', '-T', 'dmhost', timeout=120)
    print(out[-3000:])

# 8. keep or clean the remote staging dir
if not a.keep:
    hdc('shell', 'rm -rf %s/nx2a-entry' % REMOTE_DIR, timeout=120)
print('DEVPUSH DONE')
