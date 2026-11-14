# docs/ARCHITECTURE.md's rules, checked on the committed tree (tests/test_architecture.py runs it; also `python tests/arch_rules.py`).
# nx-for-ohos: the shared engine (host/gfx/compat/tools), the OHOS front end (dm/ohos), the port pipeline (porter) and the game profiles (dm/games).
import ast, re, subprocess, sys
from pathlib import Path

R = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent).resolve()
files = subprocess.run(['git', '-C', str(R), 'ls-files'], capture_output=True, text=True, check=True).stdout.splitlines()
fails, notes = [], []
def check(name, ok, detail=''):
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f": {detail}" if detail else ''))
    if not ok: fails.append(name)

def strip_c(s): return re.sub(r'//[^\n]*', '', re.sub(r'/\*.*?\*/', '', s, flags=re.S))
def strip_py(s): return re.sub(r'#[^\n]*', '', s)
profiles = sorted(p.name for p in (R / 'nxnative/dm/games').iterdir() if p.is_dir() and re.fullmatch(r'0100[0-9A-F]{12}', p.name))
names = set()
for pid in profiles:
    g = R / 'nxnative/dm/games' / pid / 'game.tsv'
    for line in g.read_text(encoding='utf-8', errors='replace').splitlines():
        k, _, v = line.partition('\t')
        if k == 'name' and len(v) > 3: names.add(v.strip())
pkgs = set()
for pid in profiles:
    a = R / 'nxnative/dm/games' / pid / 'port.tsv'
    if a.exists(): pkgs |= {l.split('\t')[1].strip() for l in a.read_text(encoding='utf-8').splitlines() if l.startswith('package\t')}
GAME_RX = re.compile(r'0100[0-9A-Fa-f]{12}|\bNEO\b|Viractal|' + '|'.join(re.escape(k) + r'\b' for k in pkgs))   # profiles' own packages, not generic defaults

# 1. OHOS front end: no title IDs, game names or per-game branches in code (game-specific fixes live in the profiles' patches.py)
hits = []
for f in files:
    if not f.startswith('nxnative/dm/ohos/') or not f.endswith(('.c', '.h', '.py')): continue
    s = (R / f).read_text(encoding='utf-8', errors='replace')
    code = strip_c(s) if f.endswith(('.c', '.h')) else strip_py(s)
    code = re.sub(r'"[^"\n]*"', lambda m: m.group(0) if GAME_RX.search(m.group(0)) or any(n in m.group(0) for n in names) else '""', code)
    for m in GAME_RX.finditer(code): hits.append(f'{f}: {m.group(0)}')
    for n in names:
        if n in code: hits.append(f'{f}: {n}')
check('dm/ohos has no game-specific code', not hits, '; '.join(hits[:5]))

# 2. Shared core: no title-ID branch in OHOS-guarded code (upstream's own per-game rules live in patches.py / sdk_patches.py)
hits = []
for f in files:
    if not re.match(r'nxnative/dm/(host|gfx|compat|tools)/', f) or not f.endswith(('.c', '.h')): continue
    code = strip_c((R / f).read_text(encoding='utf-8', errors='replace'))
    # system titles (0100000000000000..0100000000001fff: firmware programs, system data, applets) are not games
    if re.search(r'DM_OHOS|__OHOS__', code) and re.search(r'0100(?!00000000[01])[0-9A-Fa-f]{12}', code): hits.append(f)
check('shared core: no title-ID branch in OHOS-guarded code', not hits, ', '.join(hits))
diff = subprocess.run(['git', '-C', str(R), 'log', '--format=%h', '--', 'nxnative/dm/host', 'nxnative/dm/gfx', 'nxnative/dm/compat', 'nxnative/dm/tools', 'nxnative/dm/third_party'], capture_output=True, text=True).stdout.split()
notes.append(f'{len(diff)} commits touch the shared core since the subtree')

# 3. Game profiles: data only, port.tsv keys from the documented set
KEYS = {'status', 'tested', 'package', 'orientation', 'requires', 'setting', 'env'}
STATUS = {'playable', 'wip', 'boots', 'experimental'}
bad = []
for f in files:
    m = re.match(r'nxnative/dm/games/(0100[0-9A-F]{12})/(.+)', f)
    if not m: continue
    if f.endswith(('.c', '.h', '.cs', '.sh')) or (f.endswith('.py') and m.group(2) != 'patches.py'): bad.append(f)
check('profiles hold data only (code only as patches.py)', not bad, ', '.join(bad))
ptsv = [f for f in files if f.endswith('/port.tsv') and f.startswith('nxnative/dm/games/')]
bad = []
for f in ptsv:
    rows = [l.split('\t', 1) for l in (R / f).read_text(encoding='utf-8').splitlines() if l and not l.startswith('#')]
    for r in rows:
        if r[0] not in KEYS: bad.append(f'{f}: key {r[0]}')
    st = [r[1] for r in rows if r[0] == 'status']
    if len(st) != 1 or st[0] not in STATUS: bad.append(f'{f}: status {st}')
    if st in (['playable'], ['wip']) and not any(r[0] == 'tested' for r in rows): bad.append(f'{f}: {st[0]} without tested')
check(f'port.tsv keys and status valid ({len(ptsv)} profiles)', not bad, '; '.join(bad))
pp = (R / 'nxnative/dm/tools/port_profile.py').read_text(encoding='utf-8')
missing = [k for k in KEYS if f"'{k}'" not in pp]
check('port_profile.py reads every documented key', not missing, ', '.join(missing))

# 4. Port pipeline: no UI, nothing from devtools
def imports(path):
    try: t = ast.parse((R / path).read_text(encoding='utf-8'))
    except SyntaxError as e: return {f'<syntax error {e}>'}
    out = set()
    for n in ast.walk(t):
        if isinstance(n, ast.Import): out |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module: out.add(n.module)
    return out
porter = [f for f in files if f.startswith('porter/') and f.endswith('.py')]
ui = [f for f in porter if any(i.split('.')[0] in ('PySide6', 'PyQt5', 'PyQt6', 'tkinter', 'gui') for i in imports(f))]
check('porter/ imports no UI', not ui, ', '.join(ui))
dev = [f for f in porter if any(i.split('.')[0] == 'devtools' for i in imports(f))]
check('porter/ needs nothing from devtools/', not dev, ', '.join(dev))

# 5. Stages: the documented ones are the pipeline's, in order
pipe = (R / 'porter/pipeline.py').read_text(encoding='utf-8')
stages = ('inputs', 'extract', 'rewrite', 'shaders', 'host', 'hap', 'data', 'install')
check('all documented stages exist in pipeline.py', all(re.search(rf'def {st}\(self', pipe) for st in stages))
ohos = (R / 'porter/platform_ohos.py').read_text(encoding='utf-8')
check('host stage: --release unless --debug', "'--release'" in ohos and 'host-ohos-debug' in ohos)
check('porter imports upstream build_ipa_from_nsp (the shared NSP pipeline, not a copy)', any('build_ipa_from_nsp' in (R / f).read_text(encoding='utf-8') for f in porter)
      and not any(Path(f).name == 'build_ipa_from_nsp.py' for f in files if not f.startswith('nxnative/')))

# 6. Toolchains: not shipped, checked by doctor
ship = [f for f in files if re.search(r'(^|/)(android-ndk|platform-tools|dotnet)(/|$)', f) or f.startswith('tools/')]
check('no toolchain committed', not ship, ', '.join(ship[:5]))
tc = (R / 'porter/toolchain.py').read_text(encoding='utf-8')
check('doctor checks .NET, OHOS SDK, hdc, keys', all(w in tc for w in ('dotnet', 'OHOS SDK', 'hdc', 'keys')))

# 7. Keys and game files never in the repository
leak = [f for f in files if re.search(r'\.(nsp|nsz|xci|nca|keys)$|prod\.keys|title\.keys|signing-key', f, re.I)]
check('no keys, game files or signing keys committed', not leak, ', '.join(leak))
gen = [f for f in files if Path(f).name in ('game_profile.h', 'android_profile.h')]
check('generated profile headers not committed', not gen, ', '.join(gen))

# 8. Repository layout: nx-for-ohos only (no Android/iOS product toolchain)
layout = ('docs/ARCHITECTURE.md', 'porter', 'devtools', 'tests', 'requirements.txt', 'probes', 'nxnative', 'RESEARCH.md')
check('repository layout as documented', all((R / d).exists() for d in layout), ', '.join(d for d in layout if not (R / d).exists()))
gone = [f for f in files if f.startswith(('gui/', 'packaging/', 'nxnative/dm/android/', 'nxnative/dm/ios/'))]
check('no Android/iOS product toolchain left', not gone, ', '.join(gone[:5]))
check('no scripts/ folder left at the root', not any(f.startswith('scripts/') for f in files))
req = (R / 'requirements.txt').read_text(encoding='utf-8').lower()
check('requirements.txt has lz4, capstone, fonttools (no GUI)', all(m in req for m in ('lz4', 'capstone', 'fonttools')) and 'pyside6' not in req)

# 9. devtools: no hard-coded device or package
dt = [f for f in files if f.startswith('devtools/') and f.endswith('.py')]
hard = [f for f in dt if re.search(r'192\.168\.\d+\.\d+|org\.nx2a\.neo\b', strip_py((R / f).read_text(encoding='utf-8')))]
check('devtools/ has no hard-coded device or package', not hard, ', '.join(hard))
check('frozen porter finds nxnative beside the executable', 'frozen' in (R / 'porter/paths.py').read_text(encoding='utf-8'))

for n in notes: print('note ', n)
print(f'\n{len(fails)} failed' if fails else '\nall checks passed')
sys.exit(1 if fails else 0)