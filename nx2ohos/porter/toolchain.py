# The tools a port needs on the player's machine, found and checked (`porter doctor`). Nothing here is shipped with the tool: the player installs the .NET SDK (10)
# and the OHOS SDK / DevEco Studio (the host's clang and, to install from the tool, hdc). The .NET SDK can be fetched with `porter tools --download` (porter.fetch);
# the OHOS SDK and hdc come from DevEco Studio, which the finders here also search.
import glob, importlib.util, os, re, shutil, subprocess, sys
from dataclasses import dataclass
from pathlib import Path

from porter import config
from porter.paths import LOCAL_TOOLS, player_tools

DOTNET_MAJOR = 10
PY_MODULES = {'lz4.block': 'lz4', 'capstone': 'capstone'}   # the rewriter (capstone) and the NSO reader (lz4)
EXE = '.exe' if os.name == 'nt' else ''
DISK_GB = 20   # the setup check's free space: a NEO-sized port (7 GB of build folder) with room to spare


class ToolError(Exception):
    pass


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    required: bool = True
    path: str = ''
    fix: str = ''

    def as_dict(self): return dict(self.__dict__)


def find_dotnet(cfg=None):
    """The dotnet to use: the configured one as it is; otherwise the first of DOTNET_ROOT, PATH and the usual places that has SDK 10 (a runtime-only dotnet, as Windows
    installs for apps, cannot build nx_extract), else the first found"""
    cfg = cfg if cfg is not None else config.load()
    if cfg.get('dotnet') and Path(cfg['dotnet']).is_file(): return Path(cfg['dotnet'])
    progs = [os.environ.get(k) for k in ('ProgramFiles', 'ProgramW6432')] if os.name == 'nt' else []
    cands = [os.environ.get('DOTNET_ROOT') and str(Path(os.environ['DOTNET_ROOT']) / f'dotnet{EXE}'), shutil.which('dotnet'), str(LOCAL_TOOLS / 'dotnet' / f'dotnet{EXE}'), str(player_tools() / 'dotnet' / f'dotnet{EXE}'),
             str(Path.home() / '.dotnet' / f'dotnet{EXE}'), *[str(Path(p) / 'dotnet' / f'dotnet{EXE}') for p in progs if p], '/usr/local/share/dotnet/dotnet']
    found = list(dict.fromkeys(Path(c) for c in cands if c and Path(c).is_file()))
    return next((d for d in found if any(int(s.split('.')[0]) >= DOTNET_MAJOR for s in dotnet_sdks(d))), found[0] if found else None)


def dotnet_sdks(dotnet):   # {'10.0.402': '/usr/local/share/dotnet/sdk', ...}
    try: r = subprocess.run([str(dotnet), '--list-sdks'], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired): return {}
    return dict(re.findall(r'^(\d+\.\d+\.\d+)\S* \[(.+)\]', r.stdout, re.M))


def dotnet_root(dotnet):
    """The folder with dotnet's runtime, where an apphost (nx_extract) looks in DOTNET_ROOT: the SDK folder's parent, not the executable's, which on a Mac is
    a link or wrapper elsewhere (/usr/local/bin from Microsoft's installer, /opt/homebrew/bin)"""
    sdks = dotnet_sdks(dotnet)
    return Path(next(iter(sdks.values()))).parent if sdks else Path(dotnet).resolve().parent


def _has_clang(d):
    return (d / 'native/llvm/bin/clang.exe').exists() or (d / 'native/llvm/bin/clang').exists()


def find_ohos_sdk(cfg=None):
    """The OHOS SDK whose native/llvm/bin/clang builds the host: $OHOS_SDK, the usual SDK folders, then DevEco Studio's own (sdk/default/openharmony).
    The same places dm/ohos/build_ohos.py searches"""
    cfg = cfg if cfg is not None else config.load()
    deveco = [Path(h) / 'sdk/default/openharmony' for h in (os.environ.get('DEVECO_HOME'), r'E:\Program Files\Huawei\DevEco Studio',
                                                           r'D:\dev-echo\DevEco Studio', r'C:\Program Files\Huawei\DevEco Studio') if h]
    cands = ([Path(cfg['sdk'])] if cfg.get('sdk') else []) + [Path(os.environ[e]) for e in ('OHOS_SDK', 'DEVECO_SDK_HOME', 'HOS_SDK_HOME') if os.environ.get(e)]
    cands += [Path('D:/dev-echo/open-sdk'), Path.home() / 'AppData/Local/OpenHarmony/Sdk'] + deveco
    for base in cands:
        if _has_clang(base): return base
        if base.is_dir():
            for sub in sorted([p for p in base.iterdir() if p.is_dir()], reverse=True):
                if _has_clang(sub): return sub
    return None


def find_hdc(cfg=None):
    from porter import platform_ohos
    return platform_ohos.find_hdc()


def _importable(m):
    try: return importlib.util.find_spec(m) is not None
    except (ImportError, ValueError): return False


def checks(cfg=None, keys=None):
    """Every requirement, as Check rows (the order the setup screen shows them)"""
    cfg = cfg if cfg is not None else config.load()
    out = []
    v = sys.version_info
    out.append(Check('Python', v >= (3, 11), f'{v.major}.{v.minor}.{v.micro} ({sys.executable})', fix='Python 3.11 or later'))
    miss = [pip for m, pip in PY_MODULES.items() if not _importable(m)]
    if not (_importable('compression.zstd') or _importable('backports.zstd')): miss.append('backports.zstd')   # zstd: in the standard library from Python 3.14
    out.append(Check('Python modules', not miss, 'missing: ' + ', '.join(miss) if miss else 'lz4, capstone, zstd', fix='pip install ' + ' '.join(miss) if miss else ''))
    out.append(Check('Pillow', _importable('PIL'), 'the app icon from the game\'s icon' if _importable('PIL') else 'missing: the app gets no icon', required=False, fix='pip install pillow'))
    aes = _importable('Crypto.Cipher') or _importable('Cryptodome.Cipher')
    out.append(Check('pycryptodome', aes, 'NSZ / XCZ dumps (porter.dumps)' if aes else 'missing: NSZ / XCZ dumps must be decompressed to NSP / XCI first', required=False, fix='' if aes else 'pip install pycryptodome'))
    out.append(Check('fontTools', _importable('fontTools'), 'the button icons in game text' if _importable('fontTools') else 'missing: button icons in game text draw as "?"', required=False, fix='pip install fonttools'))
    dn = find_dotnet(cfg)
    sdks = dotnet_sdks(dn) if dn else []
    good = [s for s in sdks if int(s.split('.')[0]) >= DOTNET_MAJOR]
    out.append(Check('.NET SDK', bool(good), (f'{", ".join(sdks) or "no SDK"} ({dn})' if dn else 'dotnet not found'), path=str(dn or ''), fix='' if good else f'Download missing tools, or install the .NET SDK {DOTNET_MAJOR} (dotnet.microsoft.com)'))
    sdk = find_ohos_sdk(cfg)
    out.append(Check('OHOS SDK', bool(sdk), str(sdk) if sdk else 'not found: the host is built with the OHOS SDK\'s clang', path=str(sdk or ''),
                     fix='' if sdk else 'install DevEco Studio (or the OHOS SDK) and set OHOS_SDK to the folder with native/llvm/bin/clang'))
    hdc = find_hdc(cfg)
    out.append(Check('hdc', bool(hdc), str(hdc) if hdc else 'not found: the tool cannot install on a device (the HAP and data pack are still made)', required=False, path=str(hdc or ''),
                     fix='' if hdc else 'install DevEco Studio, or set HDC to its toolchains folder\'s hdc'))
    k = keys or cfg.get('keys')
    out.append(Check('prod.keys', bool(k) and Path(k).is_file(), k if k and Path(k).is_file() else (f'no such file: {k}' if k else 'not set'), path=k or '',
                     fix='' if k and Path(k).is_file() else 'dump prod.keys from your own Switch and set its path'))
    where = Path(cfg.get('out') or Path.home())   # the output folder's drive, or the home folder's (a port's build folder goes beside the game files: the port checks that drive itself)
    where = next((p for p in (where, *where.parents) if p.exists()), Path.home())
    free = shutil.disk_usage(where).free / 2**30
    out.append(Check('Disk space', free >= DISK_GB, f'{free:.0f} GB free on {where.anchor}', required=False,
                     fix='' if free >= DISK_GB else f'a port takes about twice its game files on the game folder\'s drive (NEO: 7 GB); keep {DISK_GB} GB free'))
    return out


def require(cfg=None, keys=None):
    """The checked tools for a port: {'dotnet', 'hdc'}; ToolError naming every missing requirement. The OHOS SDK's clang is checked by its own host stage
    (dm/ohos/build_ohos.py); hdc is only needed to install, so it is returned but not required here"""
    rows = checks(cfg, keys)
    bad = [r for r in rows if not r.ok and r.required]
    if bad: raise ToolError('missing requirements: ' + '; '.join(f'{r.name}: {r.detail}' + (f' ({r.fix})' if r.fix else '') for r in bad))
    by = {r.name: r for r in rows}
    return {'dotnet': Path(by['.NET SDK'].path), 'hdc': Path(by['hdc'].path) if by['hdc'].ok else None}