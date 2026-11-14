# The HarmonyOS port (porter port). The steps up to the shaders are the shared NSP pipeline's, with the rewriter's target set to
# ohos (patch_nso.py's musl thread slots). What differs is the three stages after them:
#   host     dm/ohos/build_ohos.py builds libdmhost.so with the OHOS SDK's clang
#   hap      dm/ohos/mkhap.py puts the host and the data pack in a signed HAP (the app stages the pack into its sandbox on the first launch)
#   install  hdc install -r and aa start
# The pack's layout is the shared one (mods/ RomFS/ aoc/ shaders.dmsv env.txt): front_ohos.c sets DM_ROOT / DM_ROMFS / DM_SAVE / DM_TEMP from the
# sandbox folder, and resolves the pack's relative paths (DM_AOC_DIR, DM_SHARED_FONT_EXT, ...) against it.
# The HAP's bundleName is the template's (dev.eden_emu.ohos): DevEco's automatic signing material is bound to it, and a HAP signed for another
# bundle name does not install (dm/ohos/hap/AppScope/app.json5). The game's package is recorded in the pack's manifest instead.
import os, shutil, subprocess, sys
from dataclasses import dataclass
from pathlib import Path

from porter.paths import DM
from porter.upstream import BuildError, PROG, nsp, say

OHOS = DM / 'ohos'
BUNDLE, ABILITY = 'dev.eden_emu.ohos', 'EntryAbility'


@dataclass(frozen=True)
class Stage:
    name: str
    what: str
    deps: tuple


# The stage graph, in execution order; `porter port --platform hap --dry-run` prints it.
STAGES = (
    Stage('rewrite', "patch_nso.py on the game's modules, for HarmonyOS's thread slots (DM_TARGET=ohos)", ('extract',)),
    Stage('shaders', 'the RomFS shaders into the Vulkan bundle (shaders.dmsv)', ('extract',)),
    Stage('host', 'libdmhost.so from dm/ohos/build_ohos.py (the OHOS SDK, not the NDK)', ('rewrite',)),
    Stage('datapack', 'mods/ RomFS/ aoc/ shaders.dmsv env.txt, the layout front_ohos.c reads', ('rewrite', 'shaders')),
    Stage('hap', 'the signed HAP around the host and the pack (dm/ohos/mkhap.py)', ('host', 'datapack')),
    Stage('install', 'hdc install -r, then aa start (--install)', ('hap',)),
)


def find_hdc():
    """DevEco Studio's hdc, the OHOS counterpart of adb: $HDC (the program or its folder), else <DevEco>/sdk/default/openharmony/toolchains/hdc"""
    exe = 'hdc.exe' if os.name == 'nt' else 'hdc'
    homes = (os.environ.get('DEVECO_HOME'), r'E:\Program Files\Huawei\DevEco Studio', r'D:\dev-echo\DevEco Studio', r'C:\Program Files\Huawei\DevEco Studio')
    env = os.environ.get('HDC')
    for c in (*( [os.path.join(env, exe)] if env else []), env, *[os.path.join(h, 'sdk', 'default', 'openharmony', 'toolchains', exe) for h in homes if h]):
        if c and Path(c).is_file(): return Path(c)
    return None


def host(port):
    """libdmhost.so for this profile, built by dm/ohos/build_ohos.py into the port's build folder"""
    PROG.stage('host', step='the OHOS SDK')
    out = port.aw / ('host-ohos' if not port.o.debug else 'host-ohos-debug')   # the host stage's own folder
    if out.exists(): nsp.rmtree(out)
    cmd = [sys.executable, str(OHOS / 'build_ohos.py'), '--game', port.g.id, '--lib', '--out', str(out), '--jobs', str(port.o.jobs)]
    if not port.o.debug: cmd.append('--release')
    nsp.run(cmd, port.logs / 'ohos_host.log', env=port.env)
    port.so = out / 'libdmhost.so'
    if not port.so.is_file(): raise BuildError(f'build_ohos.py made no {port.so} (see {port.logs / "ohos_host.log"})')
    PROG.end(f'{port.so.stat().st_size / 2**20:.1f} MB')


def hap(port):
    """The signed HAP around the host and the pack, as <App>.hap in the output folder"""
    PROG.stage('HAP')
    if port.o.touch: say('[RED] --touch is not in the HarmonyOS host yet: the pack carries the button layout, but nothing draws the overlay there')
    stem = port.hap_stem
    work = port.aw / 'hap'
    port.o.out.mkdir(parents=True, exist_ok=True)
    nsp.run([sys.executable, str(OHOS / 'mkhap.py'), '--game', port.g.id, '--lib', str(port.so), '--data', str(port.data_path), '--out', str(work)],
            port.logs / 'ohos_hap.log', env=port.env)
    # A pack over the 4 GB one signed HAP can carry comes back as a folder of signed HAPs (entry + data1..dataN): bm install -p takes the folder whole,
    # because hdc install refuses the app pack (it carries the unsigned HAPs)
    haps = work / 'dmhost-haps'
    if haps.is_dir():
        dest = port.o.out / f'{stem}-haps'
        if dest.exists(): shutil.rmtree(dest)
        dest.mkdir(parents=True)
        for p in haps.glob('*.hap'):
            try: os.link(p, dest / p.name)   # same disk: the 15 GB of HAPs are not copied again
            except OSError: shutil.copy2(p, dest / p.name)
        port.hap_path = dest
    else:
        src = work / 'dmhost-signed.hap'
        if not src.is_file(): raise BuildError(f'mkhap.py made no signed HAP (see {port.logs / "ohos_hap.log"})')
        port.hap_path = port.o.out / f'{stem}.hap'
        try: os.link(src, port.hap_path)
        except OSError: shutil.copy2(src, port.hap_path)
    port.result['hap'] = str(port.hap_path)
    if port.package != BUNDLE:   # the signing material is bound to the template's bundle name
        say(f'the HAP installs as {BUNDLE} (DevEco\'s signing material is bound to it); the game\'s package {port.package} is in the pack\'s manifest')
    if port.hap_path.is_dir():
        parts = sorted(port.hap_path.glob('*.hap'))
        PROG.end(f'{port.hap_path.name}/, {len(parts)} modules, {sum(p.stat().st_size for p in parts) / 2**30:.2f} GB')
    else:
        PROG.end(f'{port.hap_path.name}, {port.hap_path.stat().st_size / 2**20:.1f} MB')


def install(port):
    """Install and start on a device hdc reaches (a USB device; the AGC cloud device is not attached to hdc, its page installs the HAP)"""
    exe = find_hdc()
    if not exe: raise BuildError('hdc not found: install DevEco Studio, or set HDC to its toolchains folder\'s hdc')
    log = port.logs / 'ohos_install.log'
    target = ['-t', port.o.device] if port.o.device else []   # hdc's connect key (hdc list targets), the counterpart of adb's serial
    def run(*args, timeout=1800):
        r = subprocess.run([str(exe), *target, *args], capture_output=True, text=True, errors='replace', timeout=timeout)
        out = (r.stdout or '') + (r.stderr or '')
        with open(log, 'a', encoding='utf-8') as f: f.write(f'+ hdc {" ".join(target + list(args))}\n{out}\n')
        return r.returncode, out
    rc, out = run('list', 'targets', timeout=60)
    if rc or 'Empty' in out: raise BuildError(f'no device on hdc (see {log}): the HAP is at {port.hap_path}')
    if port.hap_path.is_dir():   # a split pack: the signed HAPs go up together and bm installs the bundle as one (hdc install takes a single HAP)
        remote = '/data/local/tmp/nx2a-install'
        run('shell', f'rm -rf {remote}; mkdir -p {remote}', timeout=60)
        for p in sorted(port.hap_path.glob('*.hap')):
            rc, out = run('file', 'send', str(p), f'{remote}/{p.name}')
            if rc or 'finish' not in out.lower(): raise BuildError(f'hdc file send failed for {p.name} (see {log}): {out.strip()[:300]}')
        # -w 600: bm's default wait is 180 s, and a 15 GB bundle takes ~25 min to unpack (code:9568392 operation time out without it); 600 s is its maximum
        rc, out = run('shell', 'bm', 'install', '-p', remote, '-u', '100', '-r', '-w', '600', timeout=3600)
        if 'successfully' not in out.lower():   # no user 100 on some builds: let bm pick the default
            rc, out = run('shell', 'bm', 'install', '-p', remote, '-r', '-w', '600', timeout=3600)
        if rc or 'successfully' not in out.lower(): raise BuildError(f'bm install failed (see {log}): {out.strip()[:400]}')
    else:
        rc, out = run('install', '-r', str(port.hap_path))
        if rc or 'success' not in out.lower(): raise BuildError(f'hdc install failed (see {log}): {out.strip()[:400]}')
    run('shell', 'aa', 'force-stop', BUNDLE, timeout=60)
    rc, out = run('shell', 'aa', 'start', '-b', BUNDLE, '-a', ABILITY, timeout=60)
    if rc or 'error' in out.lower(): say(f'[RED] aa start failed: {out.strip()[:300]} (the HAP is installed; see {log})')
    else: say(f'installed and started on the device: {port.hap_path.name}')
