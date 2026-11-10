#!/usr/bin/env python3
# Builds the HAP around libdmhost.so (the counterpart of probe/build_hap.py). Two hard constraints, both measured:
#   1. hvigor refuses a project path with non-ASCII characters, so the project is copied to an ASCII directory first;
#   2. libdmhost.so must be in entry/libs/arm64-v8a before the build, or it is not packed into libs/arm64-v8a.
# Signing is DevEco's automatic signature (the keystore password lives in the IDE's secure storage): the project is preconfigured, so the
# build produces a signed HAP as long as DevEco's signing material is the one this repo was set up with.
#   python ohos/mkhap.py [--game <title ID>] [--data <dir>] [--install]
#   --data <dir>: packed into the modules' resources/rawfile/dm (the game's RomFS and NSOs for a build that carries its own data). A data set larger than
#     --split-mb is split over feature modules (data1, data2, ...): the signing tool reads the HAP as a plain zip and refuses anything past 4 GB
#     ("find zip central directory failed"), so one module per ~3.5 GB is the only way to carry a 15 GB pack. The app reads each part through
#     createModuleContext(<module>).resourceManager (Index.ets stageData).
#   --install: also install and start it on the attached device (hdc), and wait for hilog lines
import argparse, glob, json, os, re, shutil, subprocess, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))         # dm/ohos/
DM = os.path.dirname(HERE)                                # dm/
HAP_SRC = os.path.join(HERE, 'hap')
OUT = os.path.join(HERE, 'out')
BUNDLE = 'dev.eden_emu.ohos'
ABILITY = 'EntryAbility'

ap = argparse.ArgumentParser()
ap.add_argument('--game', default='01002B30028F6000')
ap.add_argument('--lib', help="the libdmhost.so to pack (default: ohos/out/<game>/libdmhost.so; porter passes its own build's)")
ap.add_argument('--data')
ap.add_argument('--split-mb', type=int, default=3500,
                help='the most data one HAP may carry (MB); a signed HAP must stay under 4 GB (the signer is not zip64-aware)')
ap.add_argument('--install', action='store_true')
ap.add_argument('--keep-build', action='store_true')
ap.add_argument('--out', default=OUT)
a = ap.parse_args()
SO = a.lib or os.path.join(OUT, a.game, 'libdmhost.so')
if not os.path.isfile(SO): sys.exit(f'mkhap.py: {SO} is missing — run ohos/build_ohos.py --lib first')

def find_deveco():
    for p in (os.environ.get('DEVECO_HOME'), r'E:\Program Files\Huawei\DevEco Studio', r'D:\dev-echo\DevEco Studio', r'C:\Program Files\Huawei\DevEco Studio'):
        if p and os.path.isdir(os.path.join(p, 'tools', 'hvigor', 'bin')): return p
    return None
deveco = find_deveco()
if not deveco: sys.exit('mkhap.py: DevEco Studio not found (set DEVECO_HOME)')
HVIGOR = os.path.join(deveco, 'tools', 'hvigor', 'bin', 'hvigorw.bat')
NODE = os.path.join(deveco, 'tools', 'node')
HDC = os.path.join(deveco, 'sdk', 'default', 'openharmony', 'toolchains', 'hdc.exe')

def ascii_build_dir():
    cand = os.environ.get('DM_HAP_BUILD_DIR')
    if cand: return cand
    # hvigor fails on a non-ASCII path, so the project is copied to a drive root; the title ID keeps two ports from deleting each other's copy
    for d in ('F:', 'D:', 'C:'):
        if os.path.isdir(d + os.sep): return os.path.join(d + os.sep, 'ohos_build_dmhost_' + a.game)
    return os.path.join(os.path.expanduser('~'), 'ohos_build_dmhost_' + a.game)

build = ascii_build_dir()
# The folder is emptied before the copy: refuse anything that is not one of ours (DM_HAP_BUILD_DIR pointed at a wrong path would delete it)
if os.path.exists(build) and not os.path.basename(os.path.normpath(build)).startswith('ohos_build'):
    sys.exit('mkhap.py: DM_HAP_BUILD_DIR=%s does not look like a build folder (the name must start with ohos_build): nothing was deleted' % build)
shutil.rmtree(build, ignore_errors=True)
shutil.copytree(HAP_SRC, build)
app_json = os.path.join(build, 'AppScope', 'app.json5')   # the cloud debug page refuses a versionCode it already has
txt = open(app_json, encoding='utf-8').read()
# DM_VERSION_CODE overrides the clock: a device installed from another build dir may already have a higher versionCode ("install version downgrade")
vc = int(os.environ.get('DM_VERSION_CODE') or time.time())   # seconds since the epoch: grows on every build (a clock wrap after 99999 s made a build smaller than the one before), and stays
                        # under AppGallery's 2,100,000,000 limit until 2036
open(app_json, 'w', encoding='utf-8').write(re.sub(r'"versionCode"\s*:\s*\d+', '"versionCode": %d' % vc, txt, count=1))
libdir = os.path.join(build, 'entry', 'libs', 'arm64-v8a'); os.makedirs(libdir, exist_ok=True)
shutil.copy2(SO, os.path.join(libdir, 'libdmhost.so'))
MODULES = []   # [(module, files, bytes)]: entry first, then the dataN parts
DEVICE_TYPES = ['phone', 'tablet', '2in1']


def place(src, dst):   # a hardlink when the build disk is the pack's own disk (saves the copy of a 15 GB pack), else a real copy
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def make_module(name):
    """A feature module that carries nothing but rawfile/dm — no pages, no abilities: the app reads its files through createModuleContext(name)"""
    d = os.path.join(build, name)
    os.makedirs(os.path.join(d, 'src', 'main', 'resources', 'rawfile'), exist_ok=True)
    with open(os.path.join(d, 'hvigorfile.ts'), 'w', encoding='utf-8') as f:
        f.write("import { hapTasks } from '@ohos/hvigor-ohos-plugin';\n\nexport default {\n    system: hapTasks,\n    plugins:[]\n}\n")
    with open(os.path.join(d, 'oh-package.json5'), 'w', encoding='utf-8') as f:
        json.dump({'name': name, 'version': '1.0.0', 'description': "part of the port's data pack", 'main': '', 'author': '', 'license': '', 'dependencies': {}}, f, indent=2)
        f.write('\n')
    with open(os.path.join(d, 'build-profile.json5'), 'w', encoding='utf-8') as f:
        json.dump({'apiType': 'stageMode', 'buildOption': {}, 'targets': [{'name': 'default'}]}, f, indent=2)
        f.write('\n')
    with open(os.path.join(d, 'src', 'main', 'module.json5'), 'w', encoding='utf-8') as f:
        json.dump({'module': {'name': name, 'type': 'feature', 'description': "part of the port's data pack", 'deviceTypes': DEVICE_TYPES,
                              'deliveryWithInstall': True, 'installationFree': False}}, f, indent=2)
        f.write('\n')
    return os.path.join(d, 'src', 'main', 'resources', 'rawfile')


if a.data:
    # The pack is packed into the project and from there into the HAPs, so the build disk — not the game folder's — has to hold it about twice:
    # a full disk here failed mid-copytree with an IOError after minutes of copying.
    need = sum(os.path.getsize(os.path.join(d, f)) for d, _, fs_ in os.walk(a.data) for f in fs_)
    free = shutil.disk_usage(build).free
    if free < 2 * need + 512 * 2**20:
        sys.exit('mkhap.py: %s needs about %.1f GB (twice the pack) but %s has %.1f GB free: point DM_HAP_BUILD_DIR at another disk'
                 % (build, (2 * need + 512 * 2**20) / 2**30, build, free / 2**30))
    files = sorted(os.path.relpath(os.path.join(d, f), a.data).replace('\\', '/') for d, _, fs_ in os.walk(a.data) for f in fs_)

    def size(rel): return os.path.getsize(os.path.join(a.data, rel))

    # entry keeps the small fixed files (env.txt, settings.txt, shaders.dmsv, mods/, the DLC index). The RomFS — the bulk — is split over feature
    # modules: the signing tool reads a HAP as a plain zip and refuses one past 4 GB, so a pack this size cannot ride in a single HAP. Largest file
    # first, so the parts come out about even.
    fixed = [r for r in files if not r.startswith(('RomFS/', 'aoc/'))]
    bulk = sorted([r for r in files if r.startswith(('RomFS/', 'aoc/'))], key=size, reverse=True)
    limit = a.split_mb * 2**20
    big = [r for r in bulk if size(r) > limit]
    if big:   # rawfile cannot split a file: this one needs a module of its own (and would still be too big)
        sys.exit('mkhap.py: %s is %.2f GB, over the %.2f GB one HAP can carry: no way to pack it as a rawfile' % (big[0], size(big[0]) / 2**30, limit / 2**30))
    parts, used = [[]], [0]
    for r in bulk:
        if parts[-1] and used[-1] + size(r) > limit:
            parts.append([]); used.append(0)
        parts[-1].append(r); used[-1] += size(r)
    MODULES = [('entry', sorted(fixed), sum(size(r) for r in fixed))] + \
              [('data%d' % (i + 1), sorted(p), used[i]) for i, p in enumerate(parts)]
    where = {}
    for name, rels, _ in MODULES:
        dest = os.path.join(build, 'entry', 'src', 'main', 'resources', 'rawfile') if name == 'entry' else make_module(name)
        dm = os.path.join(dest, 'dm')
        os.makedirs(dm, exist_ok=True)
        for rel in rels:
            dst = os.path.join(dm, rel.replace('/', os.sep))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            place(os.path.join(a.data, rel), dst)
            where[rel] = name
    # ArkTS cannot list a rawfile directory, so the first run copies the data out file by file from this manifest (Index.ets stageData); its third column
    # names the module that carries the file. Its header is a stamp: a changed data set makes the next start overwrite files/dm again.
    h = __import__('hashlib').sha1()
    for rel in files:
        st = os.stat(os.path.join(a.data, rel))
        # size alone is not enough: a rebuilt file of the same size would keep the old copy. mtime comes with the copy (copytree uses copy2)
        h.update(('%s\t%d\t%d\n' % (rel, st.st_size, st.st_mtime_ns)).encode())
    man = os.path.join(build, 'entry', 'src', 'main', 'resources', 'rawfile', 'dm.files.tsv')
    with open(man, 'w', encoding='utf-8', newline='\n') as f:
        f.write('# DM_DATA %s\n' % h.hexdigest())
        for rel in files:
            f.write('%s\t%d\t%s\n' % (rel, size(rel), where[rel]))
    print('packed %s (%d files, %d modules, stamp %s)' % (a.data, len(files), len(MODULES), h.hexdigest()[:12]))
    for name, rels, b in MODULES:
        print('  %-6s %7d files %7.2f GB' % (name, len(rels), b / 2**30))
    if len(MODULES) > 1:   # the parts need a module entry each, or hvigor never builds them
        bp = os.path.join(build, 'build-profile.json5')
        cfg = json.load(open(bp, encoding='utf-8'))
        for name, _, _ in MODULES[1:]:
            cfg['modules'].append({'name': name, 'srcPath': './' + name, 'targets': [{'name': 'default', 'applyToProducts': ['default']}]})
        with open(bp, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, indent=2); f.write('\n')

env = dict(os.environ); env['DEVECO_SDK_HOME'] = os.path.join(deveco, 'sdk'); env['PATH'] = NODE + os.pathsep + env.get('PATH', '')
jbr = os.path.join(deveco, 'jbr')
if os.path.isdir(jbr):   # HmacPBESHA256 signing needs JBR 21: the system's Java 11 cannot sign
    env['JAVA_HOME'] = jbr; env['PATH'] = os.path.join(jbr, 'bin') + os.pathsep + env['PATH']
multi = len(MODULES) > 1
# One hvigor run per module, in module mode: project mode has no assembleHap ("Task ['assembleHap'] was not found in the project"), and assembleApp,
# which project mode does have, packs a 15.8 GB .app out of the *unsigned* copies after signing each HAP — hdc refuses that .app ("no signature file")
# and bm never reads it, so the extra zip of the whole pack is pure loss.
if multi:
    cmds = [[HVIGOR, '--mode', 'module', '-p', 'module=%s@default' % name, '-p', 'product=default', '-p', 'buildMode=debug', 'assembleHap', '--no-daemon']
            for name, _, _ in MODULES]
else:
    cmds = [[HVIGOR, '--mode', 'module', '-p', 'product=default', '-p', 'buildMode=debug', 'assembleHap', '--no-daemon']]
for cmd in cmds:
    print('+ ' + ' '.join(cmd) + f'  (cwd={build})')
    if subprocess.run(cmd, cwd=build, env=env).returncode: sys.exit('mkhap.py: hvigor failed')

os.makedirs(a.out, exist_ok=True)
if multi:
    # The signed HAPs are the install: bm install -p <folder> takes them together as one bundle (hdc install takes a single HAP, and the .app carries
    # the unsigned copies — see porter.platform_ohos.install).
    haps = sorted(glob.glob(os.path.join(build, '*', 'build', 'default', 'outputs', 'default', '*-signed.hap')))
    if not haps: sys.exit('mkhap.py: no signed module HAP produced')
    if len(haps) != len(MODULES):
        sys.exit('mkhap.py: %d of %d modules made a signed HAP — project mode did not run assembleHap in all of them' % (len(haps), len(MODULES)))
    dst = os.path.join(a.out, 'dmhost-haps')
    shutil.rmtree(dst, ignore_errors=True)
    os.makedirs(dst, exist_ok=True)
    for p in haps: place(p, os.path.join(dst, os.path.basename(p)))
    print('haps: %s (%d modules, %.2f GB)' % (dst, len(haps), sum(os.path.getsize(p) for p in haps) / 2**30))
    signed = True
else:
    pat = os.path.join(build, 'entry', 'build', '*', 'outputs', '*')
    signed, anyh = glob.glob(os.path.join(pat, '*signed*.hap')), glob.glob(os.path.join(pat, '*.hap'))
    if not anyh: sys.exit('mkhap.py: no hap produced')
    src = signed[0] if signed else anyh[0]
    dst = os.path.join(a.out, 'dmhost-signed.hap' if signed else 'dmhost-unsigned.hap')
    shutil.copy2(src, dst)
    print('hap: %s (%d bytes)%s' % (dst, os.path.getsize(dst), '' if signed else '  [UNSIGNED]'))
if not a.keep_build: shutil.rmtree(build, ignore_errors=True)
if not signed: sys.exit('mkhap.py: the hap is unsigned — open ohos/hap in DevEco once and enable automatic signing')

if a.install:
    def hdc(*args):
        r = subprocess.run([HDC] + list(args), capture_output=True, text=True, errors='replace'); return r.stdout.strip() + r.stderr.strip()
    if 'Empty' in hdc('list', 'targets'): sys.exit('mkhap.py: no device attached')
    hdc('shell', 'hilog', '-r')
    out = hdc('install', '-r', dst)   # -r: replace a previous install of the same bundle
    print('install: ' + out)
    if 'success' not in out.lower(): sys.exit('mkhap.py: install failed')
    time.sleep(3)   # the security scan rejects a start in its first seconds, and answers "success" anyway
    print('start: ' + hdc('shell', 'aa', 'start', '-b', BUNDLE, '-a', ABILITY))
