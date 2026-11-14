# The port: a game folder (NSP / XCI, or NSZ / XCZ: porter.dumps) + prod.keys + the game's profile → <App>.hap and <App>-data-ohos/ (docs/ARCHITECTURE.md "The port").
# The steps every game shares are upstream's (porter.upstream.nsp: scan, extraction, the shader split, stage reuse, progress); the HarmonyOS ones are here and in porter.platform_ohos:
# the rewrite for DM_TARGET=ohos, the Vulkan shader bundle, the host built with the OHOS SDK (dm/ohos/build_ohos.py), the signed HAP (dm/ohos/mkhap.py), the data pack and the hdc install.
# --dry-run prints the stage graph and builds nothing.
# Work files go where upstream's NSP script puts them, <game folder>/<title ID>_build/ (extract/, aoc/, shader/, logs/), with the port's own in its port/ folder (the rewrite and shader caches).
import importlib.util, os, re, shutil, sys
from dataclasses import dataclass, field
from pathlib import Path

from porter import config, datapack, dumps, fpslocker, paths, platform_ohos, profiles, toolchain
from porter.paths import DM, NXNATIVE
from porter.upstream import BuildError, PROG, game, nacp, nsp, port_profile, profile_guess, say

PY = sys.executable
# The letters of the button-icon font (make_ext_font.py knocks them out of the icons): a bold system font of the machine that ports, never shipped in the repository
LETTER_FONTS = [f'{os.environ.get("WINDIR", "C:/Windows")}/Fonts/{f}' for f in ('ARLRDBD.TTF', 'arialbd.ttf')] + [
    '/System/Library/Fonts/Supplemental/Arial Rounded Bold.ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', '/usr/share/fonts/TTF/DejaVuSans-Bold.ttf']


@dataclass
class Options:
    folder: Path                       # the game's folder: the base game, its updates and DLC
    keys: Path = None                  # prod.keys
    out: Path = None                   # where the HAP and the data pack go (default: the game folder)
    version: str = ''                  # v<N> to pack (default: the newest in the folder)
    language: str = ''                 # a code or a name (default: English)
    app_name: str = ''                 # the launcher label (default: the game's name in its control data)
    install: bool = False              # install on the device after packing
    device: str = ''                   # hdc connect key (hdc list targets)
    debug: bool = False                # a development build: debug information, debuggable, DM_CTL
    experimental: bool = False         # a game not ported yet: a provisional profile from upstream's survey, status experimental
    package: str = ''                  # the app's package instead of the profile's (recorded in the pack's manifest; the HAP installs as dev.eden_emu.ohos)
    fps60: bool = False                # 60 fps: the profile's own frame-rate cap, else the build's FPSLocker-Warehouse config (porter/fpslocker.py)
    touch: bool = False                # a game played by touch: the on-screen gamepad starts hidden (TOUCH_SETTINGS)
    alias_headroom: int = None         # GB more alias region (alias_headroom); None: ALIAS_HEADROOM for a version the profile was not brought up on, none for one it was
    platform: str = 'hap'              # hap: the HarmonyOS app with the OHOS SDK host (porter.platform_ohos)
    dry_run: bool = False              # print the stage graph and stop: nothing is extracted or built, no toolchain is required
    jobs: int = field(default_factory=lambda: os.cpu_count() or 8)


def app_stem(g, name):   # the file name of the HAP and the data pack: the profile's IPA name without .ipa (NeoTheWorldEndsWithYou), a historical naming helper
    ipa = g.get('ipa') or nsp.ipa_name(name, g.title_id)
    return ipa[:-4] if ipa.endswith('.ipa') else ipa


# --touch: the pause menu's first-launch defaults for a game played by touch (the on-screen gamepad starts hidden). As Unpacking's profile (setting overlay=0)
TOUCH_SETTINGS = {'overlay': '0'}


ALIAS_HEADROOM, ALIAS_HEADROOM_MAX = 8 << 30, 48   # the automatic headroom (bytes); the most --alias-headroom takes (GB: a 64 GB block with Engage's 5 GB layout)
def alias_headroom_gb(gb):   # --alias-headroom checked: None (automatic) or 0 .. ALIAS_HEADROOM_MAX GB
    if gb is not None and not 0 <= gb <= ALIAS_HEADROOM_MAX: raise BuildError(f'--alias-headroom {gb}: give 0 to {ALIAS_HEADROOM_MAX} GB')
    return gb
ALIAS_HINT = ('if it still crashes after "[RED] [mem] AllocateAddressRegion … refused" (0x1803), port again with a larger --alias-headroom '
              '(the Port tab\'s Extra address space)')


def alias_headroom(tsv, gb=None):
    """A profile's guest block for a game whose profile declares virtual address memory (a sys_resource row, not 0): mem_alias and mem_reserve grow by gb GB (None: ALIAS_HEADROOM,
    which a derived profile gets; the player's --alias-headroom, for any port), so the heap (the rest of the block, hle_svc.c mem_init) stays as it is. Such a game takes its
    memory as address regions in the alias region, and how it does depends on its build: the profile's alias region was sized for the tested build. The host reserves the block
    with MAP_NORESERVE, so the address space costs no memory. → (old alias, new alias), or None when the profile is left as it is (gb 0 too)"""
    add = ALIAS_HEADROOM if gb is None else gb << 30
    lines = tsv.read_text(encoding='utf-8').splitlines()
    rows = {l.split('\t', 1)[0]: i for i, l in enumerate(lines) if '\t' in l and not l.startswith('#')}
    val = lambda k, d=None: int(lines[rows[k]].split('\t')[1], 0) if k in rows else d
    if not add or not val('sys_resource', 0) or 'mem_alias' not in rows or 'mem_reserve' not in rows: return None
    alias, reserve = val('mem_alias'), val('mem_reserve')
    lines[rows['mem_alias']], lines[rows['mem_reserve']] = f'mem_alias\t{alias + add:#x}', f'mem_reserve\t{reserve + add:#x}'
    lines.insert(min(rows['mem_alias'], rows['mem_reserve']), f'# porter: {add >> 30} GB more alias region than the tested build\'s ({alias:#x}), and the block with it, for this build\'s address regions')
    tsv.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return alias, alias + add


def registered_patches():
    """{build ID: (the file, [the reason of each patch])} of the files the rewriter loads (patch_nso.py "Registered patches": dm/tools/sdk_patches.py and the profiles' patches.py;
    CUTS and OBS by their reasons, FRAME_STEP as its stubs)"""
    out = {}
    for n, p in enumerate([DM / 'tools/sdk_patches.py'] + sorted((DM / 'games').glob('*/patches.py'))):
        spec = importlib.util.spec_from_file_location(f'porter_patches_{n}', p); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
        rel = p.relative_to(DM.parent).as_posix()
        for kind in ('CUTS', 'OBS', 'FRAME_STEP'):
            for bid, v in getattr(m, kind, {}).items(): out.setdefault(bid, (rel, []))[1].extend(['frame step stubs'] if kind == 'FRAME_STEP' else [x[3] for x in v])
    return out


def lost_patches(builds, exefs, mods, table=None):
    """The modules of a derived port whose build has no registered patch while the profile's tested builds of it have some: the rewriter applies a module's patches by its build ID
    only, so the module runs without them. → ['<module> build … is not the tested …: …'], one per module"""
    table = registered_patches() if table is None else table
    out = []
    for m in mods:
        if not (exefs / m).is_file(): continue
        bid, tested = nsp.build_id(exefs / m), sorted({b[m] for b in builds.values() if b.get(m)})
        lost = [(t, *table[t]) for t in tested if t in table]
        if bid in tested or bid in table or not lost: continue
        why = '; '.join(f'{f}: ' + ', '.join(dict.fromkeys(r.split(':')[0] for r in rs)) for _, f, rs in lost)
        out.append(f'{m} build {bid[:8]}… is not the tested {", ".join(t[:8] + "…" for t in tested)} and has none of its {sum(len(rs) for _, _, rs in lost)} registered patches ({why})')
    return out


def inspect(folder, keys=None):
    """What a game folder holds, without extracting anything: `porter inspect`. keys: prod.keys (default: the configured one), which reads the title ID
    of a dump with neither a ticket nor a [title ID] tag (an untagged cartridge dump) from its NCA headers"""
    app, fam = nsp.scan_nsps(Path(folder), dumps.EXTS, keys or config.load().get('keys'))
    g, ap = profiles.by_title(app)
    files = [fam['base'], *(p for _, p in fam['updates']), *(p for _, _, p in fam['dlc'])]
    info = dict(title_id=f'{app:016X}', base=fam['base'].name, updates=[{'version': f'v{v}' if v is not None else None, 'file': p.name} for v, p in fam['updates']],
                dlc=[{'title_id': f'{t:016X}', 'file': p.name} for t, _, p in fam['dlc']], profile=None, port=None, versions=[], pack=None, supported=False, reason='',
                compressed=[p.name for p in files if dumps.compressed(p)])   # NSZ / XCZ: decompressed during the port into the build folder's tmp/, deleted once extracted
    info['experimental'] = not (g and ap)   # a port can still be tried: porter port --experimental
    if not g:
        info['reason'] = f'no game profile for {app:016X}: this game is not supported yet (an experimental port surveys it and guesses a profile; it may not start)'
        return info
    builds = g.builds()
    have = nsp.nsp_versions(fam, builds)
    pack = max(have, key=nsp.vnum)
    info.update(profile=g.id, name=g.get('name', g.id), versions=sorted(have, key=nsp.vnum), pack=pack,
                registered={v: b.get('display', '') for v, b in builds.items()}, derived=pack not in builds)
    if not ap:
        info['reason'] = f'{g.get("name", g.id)} has a game profile but is not ported to HarmonyOS yet (an experimental port tries it with that profile)'
        return info
    info.update(port=profiles.entry(g, ap).as_dict(), supported=True)
    if ap.status == 'wip': info['reason'] = f'{g.get("name", g.id)} is a work in progress: it plays, but parts of it are missing'
    if pack not in builds:
        info['reason'] = (info['reason'] + '; ' if info['reason'] else '') + f'{pack} is not a version the profile was brought up on ({", ".join(builds)}): it is packed from a derived profile, without that version\'s fixes'
    return info


class Port:
    def __init__(self, opts):
        self.o = opts
        self.cfg = config.load()
        self.result = {}
        self.unpacked = {}   # NSZ / XCZ → its NSP in <build folder>/tmp/dumps/, while the port needs it

    # ---- the whole port ----
    def run(self):
        o = self.o
        o.folder = Path(o.folder).expanduser().resolve()
        if not o.folder.is_dir(): raise BuildError(f'not a folder: {o.folder}')
        alias_headroom_gb(o.alias_headroom)   # headroom out of range fails now, not after the shaders
        keys = o.keys or self.cfg.get('keys')
        if not keys: raise BuildError('no prod.keys: give -k / --keys, or set it once with `porter config keys <path>`')
        o.keys = Path(keys).expanduser().resolve()
        o.out = Path(o.out or self.cfg.get('out') or o.folder).expanduser().resolve()
        # PYTHONUTF8: the engine's tools write their tables with open() and no encoding, which is the ANSI code page on Windows (shader_split.py's "→" failed)
        self.env = dict(os.environ, J=str(o.jobs), PYTHONIOENCODING='utf-8', PYTHONUTF8='1', DOTNET_CLI_TELEMETRY_OPTOUT='1')
        if o.dry_run:   # what the port would do, and what the folder holds: no toolchain, no extraction
            self.inputs()
            return self.dry_run()
        try: self.tools = toolchain.require(self.cfg, str(o.keys))
        except toolchain.ToolError as e: raise BuildError(str(e)) from e
        if o.install and not platform_ohos.find_hdc():   # before the host and the HAP, not after them
            raise BuildError('hdc not found: install DevEco Studio, or set HDC to its toolchains folder\'s hdc (the build itself needs neither)')
        # the chosen dotnet first on PATH, in this process too: Windows finds a program by the parent's PATH, not the one passed to the child (upstream runs `dotnet` by name)
        os.environ['PATH'] = os.pathsep.join([str(self.tools['dotnet'].parent), os.environ.get('PATH', '')])
        os.environ['DOTNET_ROOT'] = str(toolchain.dotnet_root(self.tools['dotnet']))   # where nx_extract.exe's apphost looks for the runtime (else the system's, which may lack .NET 10)

        try:
            self.inputs()
            PROG.plan(1 + len(self.versions) + bool(self.fam['dlc']) + bool(o.fps60) + 6 + bool(o.install))   # nx_extract, the versions, the DLC, 60 fps, rewrite / split / Vulkan shaders / host / HAP / data, install (unpack adds its own)
            self.extract()
        finally: self.drop_unpacked()   # a failed run leaves no decompressed dumps behind either
        self.check_graphics()
        self.fps_unlock()
        self.rewrite()
        self.shaders()
        self.host()
        self.data(); self.hap()
        if o.install: self.install()
        nsp.rmtree(self.work / 'tmp')
        self.result.update(title_id=self.g.id, name=self.app_name, package=self.package, version=self.pkg_ver, display=self.display, language=self.lang,
                           status=self.ap.status, derived=bool(self.derived), installed=bool(o.install), platform='hap')
        say(f'done: {self.result["hap"]} and {self.result["data"]}')
        if self.derived: say(f'[RED] {self.derived}: the port is built from a derived profile and may not run as the profile\'s tested version does'
                             + (f'; {self.derived_mem}' if self.derived_mem else ''))
        for x in self.lost: say(f'[RED] {x}')
        return self.result

    def dry_run(self):
        """Print the stage graph and the inputs resolved so far (the game, the version to pack, the profile, the folders), and build nothing. The gate a
        HarmonyOS port is checked by first (`porter port <folder> --dry-run`): a toolchain that is not installed yet does not fail it"""
        steps = platform_ohos.STAGES
        say(f'dry run: {self.g.id} {self.g.get("name", "")}, packing {self.pkg_ver}, package {self.package}, platform hap')
        say(f'  build folder: {self.work}')
        say(f'  output: {self.o.out}')
        try: say(f'  disk: {shutil.disk_usage(self.work).free / 2**30:.0f} GB free on {self.work.anchor}')
        except OSError: pass
        if self.derived: say(f'  [RED] {self.derived}: the profile is derived from it')
        for i, s in enumerate(steps, 1): say(f'  {i}. {s.name:9} <- {", ".join(s.deps) or "-":22} {s.what}')
        say(f'{len(steps)} stages, nothing built')
        self.result.update(dry_run=True, platform='hap', title_id=self.g.id, name=self.g.get('name', ''), package=self.package,
                           version=self.pkg_ver, stages=[s.name for s in steps])
        return self.result

    # ---- inputs: the folder's games and versions, the profile, the version to pack ----
    def inputs(self):
        o = self.o
        say(f'game folder: {o.folder}')
        self.app, self.fam = nsp.scan_nsps(o.folder, dumps.EXTS, o.keys)
        tid = f'{self.app:016X}'
        self.work = o.folder / f'{tid}_build'
        g, ap = profiles.by_title(self.app)
        self.provisional = False
        if not (g and ap):
            what = f'no game profile for {tid}' if not g else f'{g.get("name", g.id)} ({g.id}) has a game profile but is not ported to HarmonyOS yet (no port.tsv in {g.dir})'
            if not o.experimental: raise BuildError(f'{what}: --experimental tries it anyway (profiles: `porter profiles`)')
            g, ap = self.experimental_profile(g, tid)
        self.g, self.ap = g, ap
        self.package = o.package or ap.package
        if not re.fullmatch(r'[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+', self.package): raise BuildError(f'not a package name: {self.package}')
        say(f'game profile: {g.id} {g.get("name", "")} (HarmonyOS: {ap.status}' + (f', tested on {ap.tested}' if ap.tested else '') + ')')
        builds, need = g.builds(), g.words('shader_versions')
        have = nsp.nsp_versions(self.fam, builds)
        self.derived, self.derived_mem, self.lost = '', '', []
        if self.provisional:   # the version the survey saw
            self.pkg_ver = o.version or g.get('package')
            if self.pkg_ver not in builds: raise BuildError(f'the version to pack must be one the provisional profile registers: {", ".join(builds)}')
        else:
            self.pkg_ver = o.version or max(have, key=nsp.vnum)
            if self.pkg_ver not in have: raise BuildError(f'the version to pack ({self.pkg_ver}) is not in the folder, which holds {", ".join(sorted(have, key=nsp.vnum))}')
            if self.pkg_ver not in builds:   # the versions the profile registers, for the message (kept out of the f-string: nested same quotes need Python 3.12)
                known = ', '.join(v + ' ' + str(b['display']) for v, b in builds.items())
                self.derived = f'{self.pkg_ver} is not a version game profile {g.id} registers ({known})'
            elif not all(v in have for v in need): self.derived = f'the folder has no {", ".join(v for v in need if v not in have)}, whose files the profile\'s shader tables were made from'
            if self.derived: say(f'{self.derived}: packing {self.pkg_ver} from a profile derived from it')
        self.versions = [self.pkg_ver] if self.derived else need + ([self.pkg_ver] if self.pkg_ver not in need else [])
        self.have = have
        if not self.o.dry_run: self.check_space()   # a dry run answers "would this port work here": a full disk must not keep the plan from being printed
        self.aw = self.work / 'port'; self.logs = self.work / 'logs'   # the port's own folder: the rewrite and shader caches
        for d in (self.aw, self.logs, self.work / 'tmp'): d.mkdir(parents=True, exist_ok=True)
        # DM_GAME picks the profile for every tool; WORK keeps their temporary folders inside the build folder (the shader translator writes thousands of files)
        self.env.update(DM_GAME=g.id, DM_ROOT=str(self.work), WORK=str(self.work / 'tmp'))

    def check_space(self):
        """Stop before extracting when the game folder's drive cannot hold the port: a build folder takes about 1 to 2 times the game files, less what an earlier port left there,
        plus a margin; an NSZ / XCZ counts as decompressed, and once more for its NSP while it is extracted"""
        files = [f for f in self.o.folder.iterdir() if f.is_file() and f.suffix.lower()[1:] in dumps.EXTS]
        game, temp = sum(dumps.size(f) for f in files), sum(dumps.size(f) for f in files if dumps.compressed(f))
        built = sum(f.stat().st_size for f in self.work.rglob('*') if f.is_file()) if self.work.is_dir() else 0
        need, free = max(0, 2 * game + temp - built) + 2**30, shutil.disk_usage(self.o.folder).free
        if free < need: raise BuildError(f'not enough disk space on {self.o.folder.anchor}: the port needs about {need / 2**30:.1f} GB more, {free / 2**30:.1f} GB is free')

    def use_profile_dir(self, pdir, first):   # every tool finds the game's profile in pdir (game.py DM_GAMES_DIR; first: before dm/games)
        dirs = [str(pdir)] + [d for d in os.environ.get('DM_GAMES_DIR', '').split(os.pathsep) if d and d != str(pdir)]
        self.env['DM_GAMES_DIR'] = os.environ['DM_GAMES_DIR'] = os.pathsep.join(dirs)
        if first: self.env['DM_GAMES_FIRST'] = os.environ['DM_GAMES_FIRST'] = '1'

    def experimental_profile(self, g, tid):
        """A profile for a game not ported yet, in <build folder>/profile/<id>/ (outside the repository): upstream's survey and provisional profile when the game has
        none (scripts/build_ipa_from_nsp.py survey / write_profile, dm/games/profile_guess.py), or a copy of its game profile; plus a port.tsv with status experimental.
        A game brought up this way gets its profile into dm/games by hand (docs/ARCHITECTURE.md "Adding a game")"""
        pdir = self.work / 'profile'; d = pdir / tid
        if g is None:
            if (d / 'game.tsv').is_file(): say(f'no game profile for {tid}: reusing the provisional profile from the last run ({d}; delete it to survey again)')
            else:
                if self.o.dry_run: raise BuildError(f'no game profile for {tid}, and the survey that writes one unpacks the game: run the port once without --dry-run')
                ups = [p for _, p in sorted(self.fam['updates'], key=lambda u: u[0] or 0)[-1:]]   # the survey reads the base game and the newest update
                m = self.unpack([self.fam['base'], *ups])
                fam = dict(self.fam, base=m[self.fam['base']], updates=[(v, m.get(p, p)) for v, p in self.fam['updates']])
                facts = nsp.survey(self.o.folder, self.app, fam, self.o.keys, NXNATIVE, PY, self.env)
                facts['survey'] = f'<NSP folder>/{tid}_survey/survey.md'
                nsp.write_profile(d, self.app, facts)
            self.provisional = True
            say('[RED] provisional game profile: values guessed from the survey, the game may not start')
        elif not (d / 'game.tsv').is_file() or (g.dir / 'game.tsv').stat().st_mtime > (d / 'game.tsv').stat().st_mtime:   # the game profile as it is now
            nsp.rmtree(d); shutil.copytree(g.dir, d, ignore=shutil.ignore_patterns('patches.py', '__pycache__'))   # patches.py stays in dm/games, where patch_nso.py finds it
        if not (d / 'port.tsv').is_file():
            (d / 'port.tsv').write_text(f'# Written by porter for an experimental port: the game has not been brought up on HarmonyOS\nstatus\texperimental\n'
                                        f'package\torg.nx2a.t{tid.lower()}\norientation\tlandscape\n', encoding='utf-8')
        self.use_profile_dir(pdir, first=True)
        g = game.load(tid)
        return g, port_profile.PortProfile(g)

    # ---- extract: nx_extract, each version the shaders come from, the DLC ----
    def extract(self):
        o, g = self.o, self.g
        picks = nsp.pick_nsps(self.fam, self.have, self.versions)
        ex = self.work / 'extract'; ex.mkdir(parents=True, exist_ok=True)
        need = sum(dumps.size(p) * (2 if dumps.compressed(p) else 1) for p in {p for b, u in picks.values() for p in (b, u) if p}) / 2**30 + 6
        if nsp.free_gb(self.work) < need and not all((ex / v).exists() for v in self.versions): raise BuildError(f'less than {need:.0f} GB free on the volume of {self.work}')
        # NSZ / XCZ: only what a stage will read is decompressed, all in one stage before nx_extract runs (a version nsp.extract reuses reads nothing)
        fresh = lambda v: (ex / v / 'ExeFS/main').exists() and (ex / v / 'RomFS' / g.get('romfs_marker')).exists()   # nsp.extract's reuse test
        zdlc = [p for _, _, p in self.fam['dlc'] if dumps.compressed(p)]
        # extract_aoc keys its reuse on the names, sizes and times of the NSPs it reads, decompressed ones here: with NSZ / XCZ DLC, porter keys it on those files instead
        dkey = nsp.fingerprint(sorted((t, p.name, p.stat().st_size, p.stat().st_mtime_ns) for t, _, p in self.fam['dlc']),
                               ('files', [NXNATIVE / 'dm/tools/nx_extract' / f for f in ('Program.cs', 'nx_extract.csproj')]))
        dkf = self.work / 'aoc.dumps.key'
        dlc_reused = bool(zdlc) and (self.work / 'aoc').is_dir() and nsp.reused(dkf, dkey) is not None
        m = self.unpack([p for v in self.versions if not fresh(v) for p in picks[v] if p] + ([] if dlc_reused else zdlc))
        picks = {v: tuple(m.get(p, p) if p else None for p in bu) for v, bu in picks.items()}
        PROG.stage('build nx_extract')
        nx = nsp.build_nx_extract(NXNATIVE, self.work, self.logs, self.env)
        PROG.end()
        self.dirs = {ver: nsp.extract(nx, o.keys, ex, ver, *picks[ver], None, self.logs, g) for ver in self.versions}
        bad = [] if self.derived else [f'{v}: {x}' for v in self.versions for x in nsp.build_mismatch(self.dirs[v], v, g)]
        if bad and self.provisional: raise BuildError(f'not the builds the provisional profile registers: {"; ".join(bad)}')
        if bad:
            self.derived = f'not the builds game profile {g.id} registers: {"; ".join(bad)}'
            say(f'{self.derived}: packing {self.pkg_ver} from a profile derived from it')
            self.versions, self.dirs = [self.pkg_ver], {self.pkg_ver: self.dirs[self.pkg_ver]}
        self.game = self.dirs[self.pkg_ver]
        nsp.rmtree(self.work / 'profile-mem')   # an earlier run's --alias-headroom copy (mem_profile)
        if self.derived:   # the derived profile (port.tsv included) goes into the build folder, and every tool takes it first
            pdir = self.work / 'profile-derived'   # not profile/: an experimental port's copy there is the profile it derives from
            disp = nsp.derive_profile(g, pdir / g.id, self.pkg_ver, self.game, g.words('modules'))
            # automatic headroom only for another build: a registered one, derived because the folder lacks a shader version, keeps the layout it was tested with
            other = self.pkg_ver not in g.builds() or bool(nsp.build_mismatch(self.game, self.pkg_ver, g))
            gb = self.o.alias_headroom if self.o.alias_headroom is not None else (ALIAS_HEADROOM >> 30 if other else 0)
            if grown := alias_headroom(pdir / g.id / 'game.tsv', gb):
                self.derived_mem = f'its alias region has {gb} GB of headroom over the tested build\'s (mem_alias {grown[0]:#x} → {grown[1]:#x}); {ALIAS_HINT}'
                say(f'derived game profile: {self.derived_mem}' + ('' if self.o.alias_headroom is None else ' (--alias-headroom)'))
            self.use_profile_dir(pdir, first=True)
            self.g = game.load(g.id); self.ap = port_profile.PortProfile(self.g)
            say(f'derived game profile for {self.pkg_ver} ({disp}): {self.g.dir}')
            self.lost = [f'{x}: the port may fail where they are needed; register them for this build (dm/tools/nso_fnmap.py carries addresses over) or port a version the '
                         f'profile registers' for x in lost_patches(g.builds(), self.game / 'ExeFS', g.words('modules'))]
            for x in self.lost: say(f'[RED] {x}')
        else: self.mem_profile()
        if dlc_reused:
            PROG.stage('extract DLC'); self.aoc = self.work / 'aoc'; PROG.end(f'{len(self.fam["dlc"])} DLC, reused (inputs unchanged)')
        else:
            self.aoc = nsp.extract_aoc(nx, o.keys, NXNATIVE, self.work, self.app, dict(self.fam, dlc=[(t, v, m.get(p, p)) for t, v, p in self.fam['dlc']]), self.logs)
            if zdlc: dkf.write_text(f'{dkey}\n')
            else: nsp.rmtree(dkf)
        self.drop_unpacked()   # everything that reads them has run, and the shader stages that follow need the room
        self.base = next((self.dirs[v] for v in self.versions if picks[v][1] is None), self.dirs[self.versions[0]])
        self.app_name = o.app_name or nacp.name(self.base / 'control.nacp') or self.g.get('name', self.g.id)
        self.display = nacp.version(self.game / 'control.nacp') or nacp.version(self.base / 'control.nacp') or '1.0'
        lang_in = o.language or self.cfg.get('language')
        want = nsp.language_code(lang_in) if lang_in else 'en-US'
        if not want: raise BuildError(f'unknown game language {lang_in!r}')
        have = nacp.languages(self.game / 'control.nacp') or nacp.languages(self.base / 'control.nacp')
        self.lang = nsp.game_language(want, bool(lang_in), have)
        say(f'app: {self.app_name} {self.display} ({self.package}), game language {self.lang}')

    def mem_profile(self):
        """The player's --alias-headroom on a version the profile was brought up on (or a provisional or experimental profile): a copy of the profile in
        <build folder>/profile-mem/ with the bigger guest block, which every tool takes first. patches.py stays in dm/games, where patch_nso.py finds it. extract() removes
        an earlier run's copy before it, so the build folder keeps none without the option"""
        pdir = self.work / 'profile-mem'
        if not self.o.alias_headroom: return
        d = pdir / self.g.id; shutil.copytree(self.g.dir, d, ignore=shutil.ignore_patterns('patches.py', '__pycache__'))
        grown = alias_headroom(d / 'game.tsv', self.o.alias_headroom)
        if not grown:
            nsp.rmtree(pdir); say(f'--alias-headroom {self.o.alias_headroom}: {self.g.get("name", self.g.id)} declares no virtual address memory (sys_resource): its layout stays as it is'); return
        self.use_profile_dir(pdir, first=True)
        self.g = game.load(self.g.id); self.ap = port_profile.PortProfile(self.g)
        say(f'alias region: mem_alias {grown[0]:#x} → {grown[1]:#x} (+{self.o.alias_headroom} GB, --alias-headroom): {self.g.dir}')

    # ---- NSZ / XCZ: decompressed for the stages that read the game files (porter.dumps) into <build folder>/tmp/dumps/, deleted once extracted ----
    def unpack(self, paths):
        """paths' NSZ / XCZ → NSP, in one stage (a file decompressed earlier in this run is kept); returns {path: the file to read}"""
        todo = [p for p in dict.fromkeys(paths) if dumps.compressed(p) and p not in self.unpacked]
        if todo:
            d = self.work / 'tmp/dumps'; d.mkdir(parents=True, exist_ok=True)
            sizes = {p: dumps.size(p) for p in todo}; total, n = sum(sizes.values()), [0]
            if nsp.free_gb(d) < total / 2**30 + 1: raise BuildError(f'decompressing {", ".join(p.name for p in todo)} needs {total / 2**30:.1f} GB on the volume of {d}, which has {nsp.free_gb(d):.1f} GB free')
            PROG.add(1); PROG.stage('decompress NSZ / XCZ', unit='B', total=total)
            def done(k): n[0] += k; PROG.set(done=n[0])
            for p in todo:
                say(f'decompressing {p.name} ({sizes[p] / 2**30:.2f} GB) → {d}')
                self.unpacked[p] = dumps.decompress(p, d / dumps.unpacked_name(p), done)
            PROG.end(f'{len(todo)} file{"s" if len(todo) > 1 else ""}, {nsp.amount(total, "B")}, NCAs checked against their SHA-256 (not in ticketless dumps, whose NCA headers were rewritten)')
        return {p: self.unpacked.get(p, p) for p in paths}

    def drop_unpacked(self):
        if getattr(self, 'work', None): nsp.rmtree(self.work / 'tmp/dumps')
        self.unpacked = {}

    def check_graphics(self):
        """What the HarmonyOS host can run: NVN games whose shaders are all in their files. The other kinds need a step upstream runs on a Mac (booting the game in the Mac host)
        or a graphics layer the host does not have yet"""
        src = profile_guess.build_sources(self.game / 'ExeFS', self.game / 'RomFS', self.g.words('modules'))
        gfx = src['graphics'][0]
        if gfx != 'nvn': raise BuildError(f'{self.app_name} renders with {gfx} ({src["graphics"][1]}): the host replays NVN only so far')
        # Game Freak's GFPAKs with Oodle entries need the game's own decoder on a device; the HarmonyOS port has no counterpart for that tool mode yet
        self.oodle_why = src['oodle_packs'][1] if src['oodle_packs'][0] and 'GFPAK' in src['oodle_packs'][1] else ''
        if self.oodle_why:
            raise BuildError(f"{self.app_name}'s shaders are in Oodle-compressed archives ({self.oodle_why}): decoding them needs the host's tool mode on a device, "
                             f"which the HarmonyOS port has no counterpart for yet")
        for k in ('glslc_capture', 'ue_decode', 'ue_legacy', 'oodle_packs'):
            if src[k][0]: raise BuildError(f'{self.app_name} needs a step that boots the game in the Mac host ({src[k][1]}), which the HarmonyOS port cannot run yet')

    # ---- 60 fps (--fps60): the profile's own cap (fps_target: frame-step stubs or hle_fpslock.c's rows), else the community's FPSLocker config of the build ----
    def fps_unlock(self):
        self.fps_env, self.fps_files = [], {}
        self.env.pop('DM_PATCHES_EXTRA', None)
        if not self.o.fps60: return
        PROG.stage('60 fps', step='the game profile')
        if self.g.get('fps_target', '0') != '0':
            self.fps_env = ['DM_FPS_TARGET=60']; PROG.end('the game profile\'s own frame-rate cap (fps_target), at 60'); return
        img, bid = fpslocker.nso_image(self.game / 'ExeFS/main')
        src = fpslocker.url(self.g.id, bid); d = self.aw / 'fpslock'; d.mkdir(exist_ok=True); cache = d / f'{bid[:16].upper()}.yaml'
        PROG.set(step='fetching the FPSLocker-Warehouse config')
        try: text = fpslocker.fetch(self.g.id, bid)
        except OSError as e:
            if not cache.is_file(): say(f'[RED] 60 fps: {src} could not be fetched ({e}): the game keeps its own frame rate'); PROG.end('not fetched: the game keeps its own frame rate'); return
            text = cache.read_text(encoding='utf-8'); say(f'[RED] 60 fps: {src} could not be fetched ({e}): using the copy fetched before')
        if text is None:
            nsp.rmtree(cache); say(f'60 fps: FPSLocker-Warehouse has no config for main build {bid[:16].upper()}: the game keeps its own frame rate'); PROG.end('no config for this build'); return
        cache.write_text(text, encoding='utf-8')
        try: tsv, cuts = fpslocker.convert(text, img, bid, src)
        except fpslocker.Refused as e: say(f'[RED] 60 fps: {src} cannot be applied ({e}): the game keeps its own frame rate'); PROG.end('config not applicable: the game keeps its own frame rate'); return
        (d / 'fpslock.tsv').write_text(tsv, encoding='utf-8', newline='\n')
        if cuts:   # code bytes: the rewriter's cuts, applied before it rewrites main (patch_nso.py DM_PATCHES_EXTRA; part of the rewrite's reuse key)
            (d / 'patches.py').write_text(fpslocker.patches_py(bid, cuts, src), encoding='utf-8', newline='\n'); self.env['DM_PATCHES_EXTRA'] = str(d / 'patches.py')
        # FPSLocker paces the presents for its target itself, whatever interval the game asks for, so its configs leave the game's interval alone: presents follow the display (present_interval display)
        self.fps_env = ['DM_FPS_TARGET=60', f'DM_FPS_LOCKS={datapack.pack_path(self.dev_root, "fpslock.tsv")}', 'DM_PRESENT_INTERVAL=display']
        self.fps_files = {'fpslock.tsv': d / 'fpslock.tsv'}
        n = tsv.count('\n') - 3
        say(f'60 fps: FPSLocker-Warehouse config {src}, by masagrator and contributors: {n} memory write(s)' + (f', {len(cuts)} code cut(s)' if cuts else ''))
        PROG.end(f'{n} memory write(s)' + (f', {len(cuts)} code cut(s)' if cuts else ''))

    # ---- rewrite: patch_nso.py for the OHOS host's thread slots ----
    def rewrite(self):
        self.patched = self.aw / f'patched_ohos_{self.pkg_ver}'
        nsp.rewrite(PY, NXNATIVE, self.game / 'ExeFS', self.patched, dict(self.env, DM_TARGET='ohos'), self.logs, self.g)
        if self.derived or self.provisional:   # the keys that need the rewritten modules (upstream's provisional steps)
            nsp.provisional_prep(PY, NXNATIVE, self.env, self.logs, self.g)
            self.g = nsp.refine_after_rewrite(self.g, self.game, self.patched)
            if self.provisional and profile_guess.heap_check_needed(self.game / 'ExeFS') and not nsp.heap_checked(self.g):
                say('[RED] no heap check: it boots the game in the Mac host; the provisional profile keeps its guessed heap keys')

    # ---- shaders: upstream's split, then SPIR-V for the Vulkan backend ----
    def shaders(self):
        sv = self.versions
        pkg = self.work / 'shader' / 'pkg'
        nsp.split_shaders(PY, NXNATIVE, [self.dirs[v] / 'RomFS' for v in sv], [self.dirs[v] / 'ExeFS/main' for v in sv], pkg, self.env, self.logs, self.g, bool(self.derived or self.provisional),
                          oodle=None, aoc=self.aoc)   # DLC with shaders of its own (Xenoblade Chronicles 3's archives)
        self.dmsv = self.aw / 'shaders.dmsv'; kf = self.aw / 'shaders.dmsv.key'; cap = self.aw / 'captured_shaders'
        PROG.stage('Vulkan shaders', step='building the translator')
        xlate, xkey = self.translator()
        PROG.set(step='checking the inputs')
        key = nsp.fingerprint(nsp.package_key(pkg), ('files', [DM / 'tools/shader_vk.py'] + nsp.tool_files(NXNATIVE)), ('files', nsp.profile_files(self.g)), xkey,
                              ('files', sorted(cap.glob('*.bin'))))
        if self.dmsv.is_file() and (note := nsp.reused(kf, key)) is not None:
            PROG.end(f'{note}, reused (inputs unchanged)'); return
        tmp = self.dmsv.with_name('shaders.dmsv.partial'); nsp.rmtree(tmp); nsp.rmtree(kf)
        def line(s):
            if m := re.match(r'translating (\d+) shaders', s): PROG.set(step=f'translating {int(m.group(1)):,} shaders')
        nsp.run([PY, DM / 'tools/shader_vk.py', pkg, tmp, '--jobs', str(self.o.jobs), '--dotnet', self.tools['dotnet'], '--xlate', xlate, '--capture', cap],
                self.logs / 'ohos_shaders.log', env=self.env, on_line=line)
        m = re.search(r'^(\d+) of (\d+) shaders', (self.logs / 'ohos_shaders.log').read_text(errors='replace'), re.M)
        if not m or m.group(1) == '0': raise BuildError(f'no shader translated (see {self.logs / "ohos_shaders.log"})')
        note = f'{int(m.group(1)):,} of {int(m.group(2)):,} shaders, {tmp.stat().st_size / 2**20:,.0f} MB'
        nsp.swap_in(tmp, self.dmsv); kf.write_text(f'{key}\n{note}\n')
        PROG.end(note)
        for d in (self.work / 'tmp').glob('shvk_*'): nsp.rmtree(d)   # the translator's work folders

    def translator(self):
        """The shader translator (dm/gfx/tools/dmsh_xlate over dm/third_party/ryujinx) built from the repository's source with the player's .NET SDK, once for every game,
        in <user dir>/tools/dmsh_xlate: (dmsh_xlate.dll, the source's key). The port takes a translator fix as soon as it is in the source"""
        src = DM / 'gfx/tools/dmsh_xlate'; ryu = DM / 'third_party/ryujinx'
        files = sorted([*src.glob('*.cs'), *src.glob('*.csproj'), *ryu.glob('*.props')] + [p for p in (ryu / 'src').rglob('*') if p.suffix in ('.cs', '.csproj', '.props')
                                                                                         and not {'bin', 'obj'} & set(p.relative_to(ryu).parts)])
        key = nsp.fingerprint(('files', files), ('cmd', [str(self.tools['dotnet']), '--version']), env=self.env)
        out = paths.user_dir() / 'tools/dmsh_xlate'; kf = out.with_name('dmsh_xlate.key'); dll = out / 'dmsh_xlate.dll'
        if dll.is_file() and nsp.reused(kf, key) is not None: return dll, key
        tmp = out.with_name('dmsh_xlate.partial'); nsp.rmtree(tmp); nsp.rmtree(kf)
        nsp.run([self.tools['dotnet'], 'build', src, '-c', 'Release', '-o', tmp, '-nologo', '-v', 'quiet'], self.logs / 'ohos_translator_build.log', env=self.env)
        if not (tmp / 'dmsh_xlate.dll').is_file(): raise BuildError(f'the translator built without dmsh_xlate.dll (see {self.logs / "ohos_translator_build.log"})')
        nsp.swap_in(tmp, out); kf.write_text(f'{key}\n')
        return dll, key

    # ---- host: libdmhost.so built for this profile with the OHOS SDK (dm/ohos/build_ohos.py, porter.platform_ohos) ----
    def host(self):
        return platform_ohos.host(self)

    # ---- hap: the host and the pack in a signed HarmonyOS app ----
    def hap(self):
        return platform_ohos.hap(self)

    # ---- data: the files the app reads from its sandbox folder ----
    def data(self):
        PROG.stage('data pack', step='button-icon font')
        font_env, font_files = self.button_font()
        dst = self.o.out / f'{app_stem(self.g, self.app_name)}-data-ohos'
        # the pack's env.txt names its paths relative to the sandbox root (HarmonyOS), which front_ohos.c puts in front of every relative path the pack sets
        man = datapack.build(dst, profile=self.ap, version=self.pkg_ver, display=self.display, patched=self.patched, romfs=self.game / 'RomFS', shaders=self.dmsv,
                             aoc=self.aoc, language=self.lang, package=self.package, env=self.fps_env + font_env, settings=TOUCH_SETTINGS if self.o.touch else None,
                             files={**self.fps_files, **font_files}, dev_root=self.dev_root,
                             progress=lambda what: PROG.set(step=f'linking {what}'))
        self.data_path = dst; self.result['data'] = str(dst)
        PROG.end(f'{man["files"]:,} files, {man["bytes"] / 2**30:.1f} GB')
        if 'bcn' in (man.get('requires') or []):   # the Maleoon GPUs report no textureCompressionBC
            say('[RED] the profile requires BC textures, and the Maleoon GPUs report no textureCompressionBC: the game may draw wrong or crash on this device')

    def button_font(self):
        """The stand-in for the system's Nintendo extension shared font: the controller-button icons in game text (U+E0E0 = A; Super Mario Bros. Wonder's menus showed '?' without it), which
        the host serves for shared-font type 5 (hle_sdk16.c, DM_SHARED_FONT_EXT). make_ext_font.py draws it, made again when the tool or the letters change.
        Returns (env.txt rows, pack files); none without fontTools or a letter font, and the icons draw as '?'"""
        letters = next((Path(p) for p in LETTER_FONTS if Path(p).is_file()), None)
        try: import fontTools   # noqa: F401 (make_ext_font.py runs with this Python)
        except ImportError: letters, why = None, 'no fontTools (pip install fonttools)'
        else: why = 'no bold system font for the letters'
        if not letters: say(f'[RED] {why}: the button icons in the game\'s text draw as "?"'); return [], {}
        d = self.aw / 'font'; ttf, kf = d / 'nintendo_ext.ttf', d / 'nintendo_ext.key'
        key = nsp.fingerprint(('files', [DM / 'tools/make_ext_font.py', letters]))
        if not (ttf.is_file() and nsp.reused(kf, key) is not None):
            nsp.rmtree(kf); d.mkdir(parents=True, exist_ok=True)
            nsp.run([PY, DM / 'tools/make_ext_font.py', ttf, letters], self.logs / 'make_ext_font.log', env=self.env)
            kf.write_text(f'{key}\n')
        return [f'DM_SHARED_FONT_EXT={datapack.pack_path(self.dev_root, "nintendo_ext.ttf")}'], {'nintendo_ext.ttf': ttf}

    # ---- install (optional): hdc install -r, then aa start ----
    def install(self):
        return platform_ohos.install(self)

    @property
    def dev_root(self):
        """How the pack's own paths are named in env.txt: empty for HarmonyOS, whose pack root is the app's sandbox folder and so unknown when the pack is written
        (front_ohos.c puts its root in front of every relative path the pack sets)"""
        return ''

    @property
    def hap_stem(self):
        return app_stem(self.g, self.app_name)


def emit_result(r):   # the last JSON event of a port; a summary line in the other modes
    if PROG.mode == 'json': PROG._json({'ev': 'result', **r})