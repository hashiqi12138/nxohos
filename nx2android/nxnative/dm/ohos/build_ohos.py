#!/usr/bin/env python3
# HarmonyOS build of the host (the counterpart of dm/android/build.py): the same host and graphics sources, the Darwin compatibility layer
# (dm/android/compat, which is plain POSIX and is shared by both ports) and the OHOS front end / graphics backend (dm/ohos/*.c), built with the
# OHOS SDK's clang for aarch64-linux-ohos.
#   build_ohos.py --game <title ID> [--sdk <OHOS SDK dir>] [--out <dir>] [--jobs N] [--exe] [--lib] [--only-compile] [--release]
# Outputs in <out> (default dm/ohos/out/<title ID>): dmhost (an executable for the shell: headless, no window) and libdmhost.so (the HAP's native module).
import argparse, concurrent.futures as cf, hashlib, os, re, shutil, subprocess, sys
from pathlib import Path

DM = Path(__file__).resolve().parent.parent          # dm/
ap = argparse.ArgumentParser()
ap.add_argument('--game', required=True)
ap.add_argument('--sdk', default=os.environ.get('OHOS_SDK'))
ap.add_argument('--out')
ap.add_argument('--jobs', type=int, default=os.cpu_count() or 8)
ap.add_argument('--exe', action='store_true'); ap.add_argument('--lib', action='store_true')
ap.add_argument('--only-compile', action='store_true', help='compile every source and report errors, no link')
ap.add_argument('--keep-going', action='store_true')
ap.add_argument('-O', default='2')
ap.add_argument('--gfx', default='vk', choices=('vk', 'null'))
ap.add_argument('--release', action='store_true')
a = ap.parse_args()
if not (a.exe or a.lib): a.exe = a.lib = True

# ---- toolchain -----------------------------------------------------------------------------------------------------------------------------
def has_clang(d):
    return (d / 'native/llvm/bin/clang.exe').exists() or (d / 'native/llvm/bin/clang').exists()
def find_sdk():
    # DevEco Studio's own SDK (sdk/default/openharmony): the layout mkhap.py and platform_ohos.py find hdc in, so a stock install needs no variable
    deveco = [Path(h) / 'sdk/default/openharmony' for h in (os.environ.get('DEVECO_HOME'), r'E:\Program Files\Huawei\DevEco Studio',
                                                           r'D:\dev-echo\DevEco Studio', r'C:\Program Files\Huawei\DevEco Studio') if h]
    cands = ([Path(a.sdk)] if a.sdk else []) + [Path(os.environ[e]) for e in ('OHOS_SDK', 'DEVECO_SDK_HOME', 'HOS_SDK_HOME') if os.environ.get(e)]
    cands += [Path('D:/dev-echo/open-sdk'), Path.home() / 'AppData/Local/OpenHarmony/Sdk'] + deveco
    for base in cands:
        if has_clang(base): return base
        if base.is_dir():   # one level down: open-sdk/<version>, Huawei/Sdk/<api>
            for sub in sorted([p for p in base.iterdir() if p.is_dir()], reverse=True):
                if has_clang(sub): return sub
    return None
sdk = find_sdk()
if not sdk:
    sys.exit('build_ohos.py: no OHOS SDK found (pass --sdk <dir> or set OHOS_SDK; the dir holds native/llvm/bin/clang)')
sdk = sdk.resolve(); bin_ = sdk / 'native/llvm/bin'
CC = str(bin_ / ('clang.exe' if os.name == 'nt' else 'clang')); SYSROOT = str(sdk / 'native/sysroot')
TARGET = '--target=aarch64-linux-ohos'
out = Path(a.out) if a.out else DM / 'ohos/out' / (a.game + ('-release' if a.release else ''))
DBG = [] if a.release else ['-g']
obj = out / 'obj'; gen = out / 'gen'; obj.mkdir(parents=True, exist_ok=True); (gen / a.game).mkdir(parents=True, exist_ok=True)

# ---- generated headers ---------------------------------------------------------------------------------------------------------------------
subprocess.run([sys.executable, str(DM / 'games/game.py'), 'header', a.game, str(gen / a.game / 'game_profile.h')], check=True)

# the backend's own small shaders (dm/android/shaders): glslc (the NDK's, a Vulkan SDK's, or $GLSLC) → gen/<game>/<name>_<stage>.spv.h
def find_glslc():
    cands = [os.environ.get('GLSLC')]
    for base in (Path('E:/build-tool/VulkanSDK'), Path('C:/VulkanSDK'), Path(os.environ.get('ANDROID_NDK_HOME') or '/nonexistent')):
        if base and Path(base).is_dir():
            for p in sorted([Path(base)] + [d for d in Path(base).iterdir() if d.is_dir()], reverse=True):
                cands += [str(p / 'Bin/glslc.exe'), str(p / 'shader-tools/windows-x86_64/glslc.exe'), str(p / 'shader-tools/linux-x86_64/glslc')]
    return next((Path(c) for c in cands if c and Path(c).exists()), None)
GLSLC = find_glslc()
for sh in sorted((DM / 'android/shaders').glob('*.*')):
    hdr = gen / a.game / f'{sh.stem}_{sh.suffix[1:]}.spv.h'
    if hdr.exists() and hdr.stat().st_mtime > sh.stat().st_mtime: continue
    if not GLSLC: sys.exit(f'build_ohos.py: {sh.name} needs glslc (set GLSLC to a glslc binary)')
    spv = gen / f'{sh.name}.spv'
    subprocess.run([str(GLSLC), '--target-env=vulkan1.1', '-O', str(sh), '-o', str(spv)], check=True)
    data = spv.read_bytes(); n = len(data) // 4
    words = ', '.join(str(int.from_bytes(data[i * 4:i * 4 + 4], 'little')) for i in range(n))
    hdr.write_text(f'// generated from dm/android/shaders/{sh.name} by build_ohos.py (glslc)\nstatic const uint32_t {sh.stem}_{sh.suffix[1:]}_spv[] = {{{words}}};\n')

def srcs(*globs): return [p for g in globs for p in sorted(DM.glob(g))]
HOST = [p for p in srcs('host/*.c') if p.name not in ('hle_movie_avc.c',)]   # dm/ohos/movie_avc_ohos.c instead
GFX = srcs('gfx/*.c', 'gfx/nvn/*.c', 'gfx/tex/*.c')
# the Vulkan replay backend is shared with the Android port too; only its present/window file is replaced (dm/ohos/gfx_vk_present_ohos.c)
VKB = srcs('android/gfx_common.c', 'android/gfx_vk_core.c', 'android/gfx_vk_exec.c', 'android/gfx_vk_shader.c', 'android/gfx_vk_tex.c')
if a.gfx == 'null': VKB = srcs('android/gfx_common.c', 'android/gfx_null.c')
COMPAT = [p for p in srcs('android/compat/*.c') if p.name not in ('audioconverter_mediacodec.c', 'audiotoolbox_aaudio.c')]   # Android's media and audio
PORT = srcs('ohos/*.c')
if a.gfx != 'vk': PORT = [p for p in PORT if p.name != 'gfx_vk_present_ohos.c']
ASM = srcs('host/entry.S', 'gfx/nvn/nvn_entry.S', 'gfx/nvn/nvn_thunks.S')
VPX = srcs('third_party/libvpx/**/*.c')   # VP9, for the movie decoder (hle_movie.c); its own config headers, warnings off
PORT_SKIP = {'build_ohos.py'}

INC = ['-include', str(DM / 'android/compat/include/darwin_compat.h'), f'-I{DM / "android/compat/include"}', f'-I{DM / "ohos/include"}', f'-I{DM / "ohos"}', f'-I{DM / "android"}', f'-I{DM / "host"}',
       f'-I{DM / "gfx/nvn"}', f'-I{DM / "gfx/tex"}', f'-I{DM / "gfx/nvn/include"}', f'-I{gen / a.game}', f'-I{DM / "third_party/libvpx"}',
       f'-I{DM / "third_party/stb_vorbis"}']
CFLAGS = [TARGET, f'--sysroot={SYSROOT}', f'-O{a.O}'] + DBG + ['-fPIC', '-fno-omit-frame-pointer', '-ffixed-x18', '-Wall', '-Wno-unused-function', '-Wno-unused-variable',
          '-Wno-unused-but-set-variable', '-Wno-format', '-Wno-deprecated-declarations', '-Wno-gnu-folding-constant', '-Werror=implicit-function-declaration',
          '-Werror=return-type', '-Werror=incompatible-pointer-types', '-Werror=int-conversion', '-D_GNU_SOURCE', '-DDM_OHOS=1', '-fno-strict-aliasing',
          '-fvisibility=hidden'] + (['-DDM_RELEASE=1'] if a.release else [])
VPX_FLAGS = [TARGET, f'--sysroot={SYSROOT}', '-O2'] + DBG + ['-fPIC', '-ffixed-x18', f'-I{DM / "third_party/libvpx"}', f'-I{DM / "third_party/libvpx/config"}', '-w']

stamp = obj / 'flags.stamp'; flags = hashlib.sha256(repr((CFLAGS, INC, CC)).encode()).hexdigest()
if not stamp.exists() or stamp.read_text() != flags:   # compiler flags changed: every object is rebuilt
    shutil.rmtree(obj, ignore_errors=True); obj.mkdir(parents=True, exist_ok=True); stamp.write_text(flags)

def dep_stale(o, d, s):
    if not o.exists() or o.stat().st_mtime < s.stat().st_mtime: return True
    if not d.exists(): return True
    t = o.stat().st_mtime
    txt = d.read_text(errors='replace').replace('\\\n', ' ')
    for f in re.split(r'(?<!\\)\s+', txt.split(':', 1)[1] if ':' in txt else ''):
        f = f.replace('\\ ', ' ').strip()
        if f and Path(f).exists() and Path(f).stat().st_mtime > t: return True
    return False

def compile_one(s):
    rel = s.relative_to(DM); o = obj / (str(rel).replace(os.sep, '_').replace('/', '_') + '.o'); d = o.with_suffix('.d')
    src = s
    if s.suffix == '.S':   # Mach-O assembly → ELF
        src = gen / (s.stem + '.elf.S')
        if not src.exists() or src.stat().st_mtime < s.stat().st_mtime:
            subprocess.run([sys.executable, str(DM / 'android/tools/darwin2elf.py'), str(s), str(src)], check=True)
    if not dep_stale(o, d, src): return s, o, 0, ''
    if s.suffix == '.S': fl = [TARGET, f'--sysroot={SYSROOT}'] + DBG
    elif 'third_party/libvpx' in str(s).replace(os.sep, '/'): fl = VPX_FLAGS
    else: fl = CFLAGS + INC
    r = subprocess.run([CC] + fl + ['-MMD', '-MF', str(d), '-c', str(src), '-o', str(o)], capture_output=True, text=True, errors='replace')
    return s, o, r.returncode, r.stderr

def build(files):
    objs, failed = [], []
    with cf.ThreadPoolExecutor(a.jobs) as ex:
        for s, o, rc, err in ex.map(compile_one, files):
            objs.append(o)
            if rc: failed.append(s); print(f'--- {s.relative_to(DM)}\n{err.strip()}\n', file=sys.stderr)
            elif err.strip() and '--verbose' in sys.argv: print(err, file=sys.stderr)
    return objs, failed

all_files = HOST + GFX + VKB + COMPAT + PORT + ASM + VPX
objs, failed = build(all_files)
print(f'compiled {len(all_files)} sources, {len(failed)} failed' + (': ' + ', '.join(str(f.relative_to(DM)) for f in failed) if failed else ''))
if failed and not a.keep_going: sys.exit(1)
if a.only_compile: sys.exit(1 if failed else 0)

LIBS = ['-lvulkan', '-lEGL', '-lGLESv3', '-lhilog_ndk.z', '-lohaudio', '-lnative_window', '-lnative_buffer', '-lnative_vsync', '-lace_napi.z', '-lace_ndk.z',
        '-lz', '-lm', '-ldl']
LDFLAGS = [TARGET, f'--sysroot={SYSROOT}', '-fuse-ld=lld', '-Wl,--error-limit=0', '-Wl,-z,max-page-size=16384', '-Wl,--build-id', '-Wl,-z,nostart-stop-gc',
           '-Wl,--no-undefined']   # the loader on the device has no weak fallback: every symbol must resolve at link time
if a.exe:
    r = subprocess.run([CC] + LDFLAGS + ['-pie', '-o', str(out / 'dmhost')] + [str(o) for o in objs] + LIBS, capture_output=True, text=True, errors='replace')
    print(r.stderr.strip() if r.returncode else f'linked {out / "dmhost"}', file=sys.stderr if r.returncode else sys.stdout)
    if r.returncode: sys.exit(1)
if a.lib:
    r = subprocess.run([CC] + LDFLAGS + ['-shared', '-Wl,-soname,libdmhost.so', '-o', str(out / 'libdmhost.so')] + [str(o) for o in objs] + LIBS,
                       capture_output=True, text=True, errors='replace')
    print(r.stderr.strip() if r.returncode else f'linked {out / "libdmhost.so"}', file=sys.stderr if r.returncode else sys.stdout)
    if r.returncode: sys.exit(1)
    if a.release:
        subprocess.run([str(bin_ / ('llvm-strip.exe' if os.name == 'nt' else 'llvm-strip')), '--strip-unneeded', str(out / 'libdmhost.so')], check=True)
        print(f'stripped {out / "libdmhost.so"}: {(out / "libdmhost.so").stat().st_size // 1024} KB')
