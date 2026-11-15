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
# travels over http. This process serves ONE .nxpk container; `hdc rport tcp:PORT tcp:PORT` makes the device's
# loopback reach it (rport, not fport: fport listens on the device, which needs the port free there). The app
# asks /stamp first (80 bytes) and only pulls /pack.nxpk when its sandbox stamp differs, so a round whose data
# is current transfers nothing.
IMPORT_PORT = 28899


def start_pack_server(nxpk):
    """Serve /ping, /stamp (the container's 80-byte header) and /pack.nxpk (Range-capable)."""
    import http.server, socketserver, threading

    class Handler(http.server.SimpleHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'   # 1.0 closes the socket after each response, which the app reports as 2300018
        timeout = 3600                  # a 14.75 GB body over WiFi runs an hour; do not time out mid-send

        def _send_file(self, path, head_only=False):
            size = os.path.getsize(path)
            start = 0
            rng = self.headers.get('Range', '')
            if rng.startswith('bytes='):
                first = rng[len('bytes='):].split(',')[0].strip().split('-')[0]
                if first.isdigit():
                    start = min(int(first), size)
            body = size - start
            if start:
                self.send_response(206)
                self.send_header('Content-Range', 'bytes %d-%d/%d' % (start, size - 1, size))
            else:
                self.send_response(200)
            self.send_header('Content-Type', 'application/octet-stream')
            self.send_header('Content-Length', str(body))
            self.send_header('Accept-Ranges', 'bytes')
            self.end_headers()
            if head_only:
                return
            with open(path, 'rb') as f:
                if start:
                    f.seek(start)
                while True:
                    buf = f.read(1 << 20)
                    if not buf:
                        break
                    try:
                        self.wfile.write(buf)
                    except (BrokenPipeError, ConnectionResetError):
                        return   # the app went away; its .part keeps what arrived
                try:
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass

        def do_HEAD(self):
            if self.path == '/pack.nxpk':
                self._send_file(nxpk, head_only=True)
                return
            self.send_response(404)
            self.end_headers()

        def do_GET(self):
            if self.path == '/ping':
                body = b'PONG'
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path == '/stamp':
                with open(nxpk, 'rb') as f:
                    body = f.read(80)
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path == '/pack.nxpk':
                self._send_file(nxpk)
                return
            self.send_response(404)
            self.end_headers()

        def log_message(self, *a):
            pass

    socketserver.ThreadingTCPServer.allow_reuse_address = True
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
    # Build (or reuse) the container. pack_game.py writes it beside the pack and validates the header; a cached
    # one is reused when its stamp still matches the pack's manifest, so repeated data rounds do not re-tar 14 GB.
    nxpk = os.path.join(os.path.dirname(pack), a.game + '.nxpk')
    rebuild = True
    if os.path.isfile(nxpk):
        try:
            with open(nxpk, 'rb') as f:
                hdr = f.read(80)
            cached_stamp = hdr[24:24 + 48].rstrip(b'\x00').decode('ascii', 'replace')
            man_probe = os.path.join(pack, 'dm.files.tsv')
            if os.path.isfile(man_probe):
                with open(man_probe, encoding='utf-8', errors='replace') as f:
                    first = f.readline().strip()
                cur_stamp = first[len('# DM_DATA '):].strip() if first.startswith('# DM_DATA ') else ''
                if cur_stamp and cur_stamp == cached_stamp:
                    rebuild = False
                    print('reusing the container %s (stamp %s)' % (nxpk, cached_stamp[:12]))
        except Exception:
            rebuild = True
    if rebuild:
        t0 = time.time()
        rc, out = run([sys.executable, os.path.join(DM, 'tools', 'pack_game.py'),
                       '--pack', pack, '--title', a.game, '--out', nxpk], timeout=3600)
        if rc: sys.exit('devpush: pack_game failed:\n' + out[-800:])
        print('container built in %.0f s: %s' % (time.time() - t0, out.strip().splitlines()[-1]))
    srv = start_pack_server(nxpk)
    print('pack server on 127.0.0.1:%d serving %s' % (IMPORT_PORT, os.path.basename(nxpk)))
    hdc('rport', 'tcp:%d' % IMPORT_PORT, 'tcp:%d' % IMPORT_PORT, timeout=60)
    rc, out = hdc('fport', 'ls', timeout=60)
    print('forward:', 'tcp:%d' % IMPORT_PORT in out and 'Reverse' in out and 'ok' or out.strip()[:100])
    globals()['_pack_server'] = srv   # kept alive until the process ends
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

# 9. --data-dir keeps this process alive: the pack server IS the data source, so exiting takes the import with
# it. Without a start the app may already be running and about to ask for /stamp; with one, the watch loop above
# has finished (game started or the budget ran out). Either way, serve until Ctrl+C.
if a.data_dir and '_pack_server' in globals():
    print('serving the pack on 127.0.0.1:%d - Ctrl+C to stop' % IMPORT_PORT)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print('stopped')
print('DEVPUSH DONE')
