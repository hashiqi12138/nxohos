# Unit tests of the port pipeline's own parts (python -m unittest discover tests). They need no game files, toolchains or device.
import hashlib, importlib.util, json, os, random, re, shutil, struct, sys, tempfile, unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.environ['NX2A_HOME'] = tempfile.mkdtemp(prefix='nx2a_test_home_')

from porter import config, datapack, pipeline, platform_ohos, profiles   # noqa: E402
from porter.upstream import port_profile, game          # noqa: E402

# The pipeline's messages hold "→" (mem_profile's alias region line): stdout as porter.cli.main sets it, or the Windows release runner's cp1252 pipe fails the test
if hasattr(sys.stdout, 'reconfigure'): sys.stdout.reconfigure(encoding='utf-8', errors='replace')

NEO = '010043B013C5C000'


def profile_dir(rows, gid='0100000000AAA000'):
    """A throwaway game profile with a port.tsv of these rows, found through DM_GAMES_DIR"""
    root = Path(tempfile.mkdtemp(prefix='nx2a_test_profiles_')); d = root / gid; d.mkdir()
    (d / 'game.tsv').write_text(f'title_id\t{gid}\nprogram_id\t{gid}\nname\tTest Game\nipa\tTestGame.ipa\nmodules\tmain sdk\n', encoding='utf-8')
    (d / 'port.tsv').write_text(''.join(f'{k}\t{v}\n' for k, v in rows), encoding='utf-8')
    os.environ['DM_GAMES_DIR'] = str(root)
    return d


class PortProfiles(unittest.TestCase):
    def tearDown(self): os.environ.pop('DM_GAMES_DIR', None)

    def test_neo(self):
        p = profiles.load(NEO)
        self.assertEqual(p.status, 'playable'); self.assertEqual(p.package, 'org.nx2a.neo'); self.assertTrue(p.landscape)
        self.assertEqual(p.settings, {'docked': '0', 'overlay': '1', 'rumble': '1'}); self.assertEqual(p.requires, ['bcn'])

    def test_only_ported_profiles_listed(self):
        rows = profiles.all_entries()
        self.assertIn(NEO, [r.id for r in rows]); self.assertTrue(all(r.ported for r in rows))
        self.assertGreater(len(profiles.all_entries(ported_only=False)), len(rows))

    def test_fps_setting(self):   # the pause menu's Show FPS (front_ohos.c settings_load fps=)
        d = profile_dir([('status', 'boots'), ('setting', 'fps=1')])
        self.assertEqual(port_profile.load(d.name).settings, {'fps': '1'})
        with self.assertRaises(port_profile.ProfileError): port_profile.load(profile_dir([('status', 'boots'), ('setting', 'fps=2')]).name)

    def test_opacity_setting(self):   # the gamepad's opacity, 20..80 percent by 5 (the menu's slider)
        self.assertEqual(port_profile.load(profile_dir([('status', 'boots'), ('setting', 'opacity=45')]).name).settings, {'opacity': '45'})
        for v in ('10', '85', '42'):
            with self.assertRaises(port_profile.ProfileError, msg=v): port_profile.load(profile_dir([('status', 'boots'), ('setting', f'opacity={v}')]).name)

    def test_default_package(self):
        d = profile_dir([('status', 'boots')])
        self.assertEqual(port_profile.load(d.name).package, 'org.nx2a.t0100000000aaa000')

    def test_bad_keys_are_refused(self):
        for rows in ([('status', 'great')], [('colour', 'red')], [('setting', 'volume=3')], [('overlay', 'tablet q 0.1 0.2')], [('env', 'lower=1')], [('package', 'Not A Package')],
                     [('status', 'boots'), ('status', 'playable')]):
            d = profile_dir(rows)
            with self.assertRaises(port_profile.ProfileError, msg=rows): port_profile.load(d.name)


class DataPack(unittest.TestCase):
    def test_build(self):
        t = Path(tempfile.mkdtemp(prefix='nx2a_test_pack_'))
        try:
            (t / 'patched').mkdir(); (t / 'patched/main.patched.bin').write_bytes(b'x' * 10)
            (t / 'romfs/Data').mkdir(parents=True); (t / 'romfs/Data/a.bin').write_bytes(b'y' * 20)
            (t / 'aoc/1').mkdir(parents=True); (t / 'aoc/1/d.bin').write_bytes(b'z')
            (t / 'shaders.dmsv').write_bytes(b'DMSV')
            p = profiles.load(NEO)
            man = datapack.build(t / 'out-data', profile=p, version='v196608', display='1.03', patched=t / 'patched', romfs=t / 'romfs', shaders=t / 'shaders.dmsv',
                                 aoc=t / 'aoc', language='ja')
            o = t / 'out-data'
            self.assertTrue((o / 'mods/main.patched.bin').is_file()); self.assertTrue((o / 'RomFS/Data/a.bin').is_file()); self.assertTrue((o / 'aoc/1/d.bin').is_file())
            env = (o / 'env.txt').read_text()
            self.assertIn('DM_LANG=ja', env); self.assertIn('DM_AOC_DIR=aoc', env)   # the app prefixes its sandbox folder (front_ohos.c)
            self.assertEqual((o / 'settings.txt').read_text(), 'docked=0\noverlay=1\nrumble=1\n')
            self.assertEqual((man['files'], man['bytes'], man['dlc'], man['requires']), (4, 35, [1], ['bcn']))   # the installer checks requires
            self.assertEqual(datapack.read_manifest(o)['package'], 'org.nx2a.neo')
            datapack.build(t / 'touch-data', profile=p, version='v196608', display='1.03', patched=t / 'patched', romfs=t / 'romfs', shaders=t / 'shaders.dmsv',
                           settings=pipeline.TOUCH_SETTINGS)   # --touch: the gamepad starts hidden, the profile's other settings stay
            self.assertEqual((t / 'touch-data/settings.txt').read_text(), 'docked=0\noverlay=0\nrumble=1\n')
            bare = port_profile.load(profile_dir([('status', 'experimental')]).name); os.environ.pop('DM_GAMES_DIR', None)   # no setting rows: --touch alone still writes the file
            datapack.build(t / 'bare-data', profile=bare, version='v196608', display='1.03', patched=t / 'patched', romfs=t / 'romfs', shaders=t / 'shaders.dmsv', settings=pipeline.TOUCH_SETTINGS)
            self.assertEqual((t / 'bare-data/settings.txt').read_text(), 'overlay=0\n')
        finally:
            shutil.rmtree(t, ignore_errors=True)

    @unittest.skipUnless(any(Path(p).is_file() for p in pipeline.LETTER_FONTS), 'no letter font on this machine')
    def test_button_font(self):   # the Nintendo extension stand-in: made once, in the pack, named in env.txt (Super Mario Bros. Wonder's "Press L+R" drew '?' without it)
        try: from fontTools.ttLib import TTFont
        except ImportError: self.skipTest('no fontTools')
        t = Path(tempfile.mkdtemp(prefix='nx2a_test_font_'))
        try:
            port = pipeline.Port(pipeline.Options(folder=str(t)))
            port.package = 'org.nx2a.test'; port.aw = t / 'port'; port.logs = t / 'logs'; port.env = dict(os.environ, PYTHONUTF8='1')
            env, files = pipeline.Port.button_font(port)
            self.assertEqual(env, ['DM_SHARED_FONT_EXT=nintendo_ext.ttf']); self.assertEqual(list(files), ['nintendo_ext.ttf'])   # bare: front_ohos.c prefixes the sandbox folder
            cmap = TTFont(files['nintendo_ext.ttf']).getBestCmap()
            self.assertTrue({0xE0E0, 0xE0E1, 0xE0E4, 0xE0E5, 0xE0EF, 0xE100} <= set(cmap))   # A, B, L, R, +, stick
            made = files['nintendo_ext.ttf'].stat().st_mtime_ns
            self.assertEqual(pipeline.Port.button_font(port)[1]['nintendo_ext.ttf'].stat().st_mtime_ns, made)   # reused while the tool and the letters are unchanged
        finally:
            shutil.rmtree(t, ignore_errors=True)


class HarmonyOSPort(unittest.TestCase):   # porter/platform_ohos.py: the stage graph --dry-run prints, and the pack whose paths the app resolves
    @staticmethod
    def folder(t):
        # a base game and an update of Celeste: the title IDs come from the names, so no prod.keys and no readable NSP are needed
        (t / 'Celeste [01002B30028F6000][v0].nsp').write_bytes(b'x' * 64)
        (t / 'Celeste [01002B30028F6800][v589824].nsp').write_bytes(b'x' * 64)
        k = t / 'prod.keys'; k.write_text('')
        return t

    def test_dry_run_lists_every_stage(self):   # the gate a HarmonyOS port is checked by first: no toolchain, nothing extracted or written to the output folder
        t = self.folder(Path(tempfile.mkdtemp(prefix='nx2a_test_hap_')))
        try:
            r = pipeline.Port(pipeline.Options(folder=str(t), keys=str(t / 'prod.keys'), out=str(t / 'out'), platform='hap', dry_run=True)).run()
            self.assertTrue(r['dry_run']); self.assertEqual(r['platform'], 'hap'); self.assertEqual(r['version'], 'v589824')
            self.assertEqual(r['stages'], [s.name for s in platform_ohos.STAGES])
            self.assertFalse((t / 'out').exists())
        finally:
            shutil.rmtree(t, ignore_errors=True)

    def test_pack_paths_are_relative(self):   # a HarmonyOS pack names its own files bare: front_ohos.c puts the sandbox folder in front of them at startup
        self.assertEqual(datapack.pack_path('', 'aoc'), 'aoc')
        self.assertEqual(datapack.pack_path('/data/app/files', 'aoc'), '/data/app/files/aoc')
        t = Path(tempfile.mkdtemp(prefix='nx2a_test_hap_pack_'))
        try:
            (t / 'patched').mkdir(); (t / 'patched/main.patched.bin').write_bytes(b'x' * 10)
            (t / 'romfs').mkdir(); (t / 'aoc').mkdir()
            (t / 'shaders.dmsv').write_bytes(b'DMSV')
            man = datapack.build(t / 'data', profile=profiles.load(NEO), version='v196608', display='1.03', patched=t / 'patched', romfs=t / 'romfs',
                                 shaders=t / 'shaders.dmsv', aoc=t / 'aoc', dev_root='')
            self.assertNotIn('/sdcard', (t / 'data/env.txt').read_text())
            self.assertIn('DM_AOC_DIR=aoc', (t / 'data/env.txt').read_text())
            self.assertEqual(man['package'], 'org.nx2a.neo')
        finally:
            shutil.rmtree(t, ignore_errors=True)


class Experimental(unittest.TestCase):
    def test_profile_copied_with_experimental_port_tsv(self):   # a game profile without a port.tsv is copied out of the repository, never edited in place
        unported = '010028600EBDA000'   # Super Mario 3D World + Bowser's Fury: a game profile without port.tsv or patches.py
        t = Path(tempfile.mkdtemp(prefix='nx2a_test_exp_')); environ = dict(os.environ)
        try:
            port = pipeline.Port(pipeline.Options(folder=t, experimental=True)); port.env = {}; port.work = t / f'{unported}_build'; port.o.keys = None
            g, ap = port.experimental_profile(game.load(unported), unported)
            self.assertEqual(g.dir, (t / f'{unported}_build/profile/{unported}').resolve())
            self.assertEqual((ap.status, ap.package), ('experimental', f'org.nx2a.t{unported.lower()}'))
            self.assertFalse((REPO / f'nxnative/dm/games/{unported}/port.tsv').exists())
            self.assertFalse((g.dir / 'patches.py').exists())   # patch_nso.py finds the per-build cuts in dm/games
            self.assertEqual(os.environ.get('DM_GAMES_FIRST'), '1')
        finally:
            os.environ.clear(); os.environ.update(environ); shutil.rmtree(t, ignore_errors=True)


class Misc(unittest.TestCase):
    def test_config_roundtrip(self):
        config.set_value('device', 'abc:5555'); self.assertEqual(config.load()['device'], 'abc:5555')
        with self.assertRaises(KeyError): config.set_value('nope', 'x')

    def test_derived_alias_headroom(self):   # issue #26: a derived profile of a game with virtual address memory gets 8 GB more alias region, the heap unchanged
        d = profile_dir([('status', 'playable')])
        try:
            t = d / 'game.tsv'; base = t.read_text(encoding='utf-8')
            t.write_text(base + '# memory\nmem_reserve\t0x140000000\nmem_alias\t0x118000000\nmem_stack\t0x10000000\nsys_resource\t0x1000000\n', encoding='utf-8')
            self.assertEqual(pipeline.alias_headroom(t), (0x118000000, 0x318000000))
            g = game.load(d.name)
            self.assertEqual((g.int('mem_reserve'), g.int('mem_alias'), g.int('mem_stack')), (0x340000000, 0x318000000, 0x10000000))
            self.assertIn('# porter: 8 GB more alias region', t.read_text(encoding='utf-8'))
            vam = base + 'mem_reserve\t0x140000000\nmem_alias\t0x118000000\nmem_stack\t0x10000000\nsys_resource\t0x1000000\n'
            t.write_text(vam, encoding='utf-8'); self.assertEqual(pipeline.alias_headroom(t, 16), (0x118000000, 0x518000000))   # --alias-headroom 16
            self.assertIn('# porter: 16 GB more alias region', t.read_text(encoding='utf-8'))
            t.write_text(vam, encoding='utf-8'); self.assertIsNone(pipeline.alias_headroom(t, 0)); self.assertEqual(t.read_text(encoding='utf-8'), vam)   # 0: as it is
            for rows in ('sys_resource\t0x0\n', ''):   # no virtual address memory, or a profile older than the key (heap games): as it is
                t.write_text(base + 'mem_reserve\t0x100000000\nmem_alias\t0x10000000\nmem_stack\t0x10000000\n' + rows, encoding='utf-8')
                self.assertIsNone(pipeline.alias_headroom(t)); self.assertIsNone(pipeline.alias_headroom(t, 16))
                self.assertEqual(game.load(d.name).int('mem_alias'), 0x10000000)
        finally: shutil.rmtree(d.parent, ignore_errors=True); os.environ.pop('DM_GAMES_DIR', None)

    def test_alias_headroom_option(self):   # --alias-headroom: parsed, checked before any stage, and kept as None (automatic) when not given
        from porter import cli
        self.assertEqual(cli._parser().parse_args(['port', 'x', '--alias-headroom', '16']).alias_headroom, 16)
        self.assertIsNone(cli._parser().parse_args(['port', 'x']).alias_headroom)
        for ok in (None, 0, pipeline.ALIAS_HEADROOM_MAX): self.assertEqual(pipeline.alias_headroom_gb(ok), ok)
        t = Path(tempfile.mkdtemp(prefix='nx2a_test_game_'))
        try:
            for bad in (-1, pipeline.ALIAS_HEADROOM_MAX + 1):
                with self.assertRaises(pipeline.BuildError) as e: pipeline.Port(pipeline.Options(folder=t, alias_headroom=bad)).run()
                self.assertIn('--alias-headroom', str(e.exception))
        finally: shutil.rmtree(t, ignore_errors=True)

    def test_mem_profile(self):   # --alias-headroom on a version the profile was brought up on: a copy in profile-mem/ in front, without patches.py; none without the option
        d = profile_dir([('status', 'playable')]); environ = dict(os.environ); t = Path(tempfile.mkdtemp(prefix='nx2a_test_build_'))
        try:
            (d / 'game.tsv').write_text((d / 'game.tsv').read_text(encoding='utf-8') + 'mem_reserve\t0x140000000\nmem_alias\t0x118000000\nmem_stack\t0x10000000\nsys_resource\t0x1000000\n',
                                        encoding='utf-8')
            (d / 'patches.py').write_text('CUTS = {}\n', encoding='utf-8')
            port = pipeline.Port(pipeline.Options(folder=t, alias_headroom=16)); port.env = {}; port.work = t; port.g = game.load(d.name)
            port.mem_profile()
            self.assertEqual(port.g.dir, (t / 'profile-mem' / d.name).resolve())
            self.assertEqual((port.g.int('mem_alias'), port.g.int('mem_reserve')), (0x518000000, 0x540000000))
            self.assertFalse((port.g.dir / 'patches.py').exists()); self.assertEqual(os.environ.get('DM_GAMES_FIRST'), '1')
            shutil.rmtree(t / 'profile-mem')   # as extract() does before it
            port.o.alias_headroom = None; port.g = game.load(d.name); port.mem_profile()
            self.assertFalse((t / 'profile-mem').exists()); self.assertEqual(port.g.dir, d.resolve())
        finally:
            os.environ.clear(); os.environ.update(environ); shutil.rmtree(d.parent, ignore_errors=True); shutil.rmtree(t, ignore_errors=True)

    def test_lost_patches(self):   # issue #26: a derived module of another build than the tested one, which has cuts, is named; one with cuts of its own, or no cuts lost, is not
        d = Path(tempfile.mkdtemp(prefix='nx2a_test_exefs_'))
        try:
            for m, bid in (('main', 'aa' * 16), ('sdk', 'bb' * 16), ('subsdk0', 'cc' * 16)): (d / m).write_bytes(b'NSO0' + bytes(0x3c) + bytes.fromhex(bid))
            builds = {'v65536': {'display': '1.1.0', 'main': '11' * 16, 'sdk': '22' * 16, 'subsdk0': 'cc' * 16}}
            table = {'22' * 16: ('dm/tools/sdk_patches.py', ['AddressRegionManager constructor: node area limit', 'AddressRegionManager constructor: length']), '33' * 16: ('x', ['y'])}
            lost = pipeline.lost_patches(builds, d, ['main', 'sdk', 'subsdk0'], table)
            self.assertEqual(len(lost), 1); self.assertTrue(lost[0].startswith('sdk build bbbbbbbb… is not the tested 22222222…'))
            self.assertIn('2 registered patches (dm/tools/sdk_patches.py: AddressRegionManager constructor)', lost[0])
            table['bb' * 16] = ('dm/tools/sdk_patches.py', ['z'])   # the derived build has cuts of its own
            self.assertEqual(pipeline.lost_patches(builds, d, ['main', 'sdk', 'subsdk0'], table), [])
            self.assertIn('6075151627016eef437c1312f2e3ed56', pipeline.registered_patches())   # the real files load
        finally: shutil.rmtree(d, ignore_errors=True)

    def test_app_stem(self):
        self.assertEqual(pipeline.app_stem(game.load(NEO), 'NEO'), 'NeoTheWorldEndsWithYou')

    def test_disk_space(self):   # twice the game files, less what the build folder holds, plus 1 GB
        from types import SimpleNamespace as NS
        d = Path(tempfile.mkdtemp(prefix='nx2a_test_game_')); (d / 'g.nsp').write_bytes(b'\0' * 2**20); work = d / 'build'
        p = NS(o=NS(folder=d), work=work); real = shutil.disk_usage
        try:
            shutil.disk_usage = lambda _: NS(free=2**30 + 2**20)
            with self.assertRaises(pipeline.BuildError): pipeline.Port.check_space(p)
            work.mkdir(); (work / 'x').write_bytes(b'\0' * 2**20); pipeline.Port.check_space(p)   # a build folder already there counts
        finally: shutil.disk_usage = real


def _hfs0(files):   # an HFS0 of (name, data): the XCI's partitions (hash fields left zero: nothing here reads them)
    strs = b''.join(n.encode() + b'\0' for n, _ in files); ent, off = b'', 0
    for i, (n, d) in enumerate(files):
        ent += struct.pack('<QQII8x32s', off, len(d), sum(len(m.encode()) + 1 for m, _ in files[:i]), 0, b''); off += len(d)
    return struct.pack('<4sII4x', b'HFS0', len(files), len(strs)) + ent + strs + b''.join(d for _, d in files)


@unittest.skipUnless(importlib.util.find_spec('Crypto'), 'no pycryptodome')
class Dumps(unittest.TestCase):   # porter/dumps.py: NSZ / XCZ → NSP; an NCZ made here as nsz makes it, from an NCA of a plain section and an AES-CTR one
    def setUp(self):
        from Crypto.Cipher import AES
        from porter import dumps; self.dumps = dumps
        rnd = random.Random(7); b = lambda n: bytes(rnd.getrandbits(8) for _ in range(n))
        self.key, self.ctr = b(16), b(8) + b'\0' * 8
        head, plain = b(0x4000), b(0x2000) + bytes(0x18000)   # the CTR part compresses (zeros), so zstd has something to do
        self.nca = head + plain[:0x2000] + AES.new(self.key, AES.MODE_CTR, nonce=self.ctr[:8], initial_value=0x6000 >> 4).encrypt(plain[0x2000:])
        self.name = hashlib.sha256(self.nca).hexdigest()[:32]
        secs = struct.pack('<8sQ', b'NCZSECTN', 2) + struct.pack('<QQQQ16s16s', 0x4000, 0x2000, 1, 0, bytes(16), bytes(16)) + struct.pack('<QQQQ16s16s', 0x6000, 0x18000, 3, 0, self.key, self.ctr)
        z = dumps.zstd_mod()
        self.solid = head + secs + z.compress(plain)
        bs = 1 << 14; blocks = [plain[i:i + bs] for i in range(0, len(plain), bs)]
        comp = [z.compress(k) if i else k for i, k in enumerate(blocks)]   # the first block stored as it is (random data: compressing does not shrink it)
        self.block = head + secs + struct.pack('<8sBBBBIQ', b'NCZBLOCK', 2, 1, 0, 14, len(blocks), len(plain)) + struct.pack(f'<{len(blocks)}I', *map(len, comp)) + b''.join(comp)
        self.d = Path(tempfile.mkdtemp(prefix='nx2a_test_dumps_'))

    def nsz(self, ncz, name='g.nsz'):
        files = [(self.name + '.ncz', ncz), ('0100000000aaa0000000000000000000.tik', b't' * 0x2c0)]
        hdr = self.dumps._pfs0_header([n for n, _ in files], [len(d) for _, d in files])
        (self.d / name).write_bytes(hdr + b''.join(d for _, d in files)); return self.d / name

    def check(self, src):
        self.assertEqual(self.dumps.size(src), len(self.dumps._pfs0_header([self.name + '.nca', 'x' * 36], [0, 0])) + len(self.nca) + 0x2c0)
        out = self.dumps.decompress(src, self.d / self.dumps.unpacked_name(src))
        self.assertEqual(out.stat().st_size, self.dumps.size(src)); self.assertEqual(out.stat().st_mtime_ns, src.stat().st_mtime_ns)
        with open(out, 'rb') as f: got = {n: (f.seek(o), f.read(s))[1] for n, o, s in self.dumps.entries(out)}
        self.assertEqual(got[self.name + '.nca'], self.nca); self.assertEqual(len(got), 2)

    def test_solid_and_block(self):
        self.check(self.nsz(self.solid)); self.check(self.nsz(self.block, 'b.nsz'))

    def test_first_section_in_the_header(self):   # nsz 5.0 starts the first section at 0xc00, inside the header it keeps as it is (a DLC NSZ it made, 2026-10-29)
        s = bytearray(self.solid); s[0x4000 + 16:0x4000 + 32] = struct.pack('<QQ', 0xc00, 0x2000 + 0x4000 - 0xc00)
        self.check(self.nsz(bytes(s)))

    def test_xcz_is_its_secure_partition(self):
        secure = _hfs0([(self.name + '.ncz', self.block), ('0100000000aaa0000000000000000000.tik', b't' * 0x2c0)])
        root = _hfs0([('update', _hfs0([])), ('secure', secure)])
        x = bytearray(0x200); x[0x100:0x104] = b'HEAD'; x[0x130:0x138] = struct.pack('<Q', 0x200)
        (self.d / 'g.xcz').write_bytes(bytes(x) + root); self.check(self.d / 'g.xcz')

    def test_damage_is_found(self):   # a changed byte: a zstd error, or an NCA whose SHA-256 is not its name; either way a BuildError and no NSP
        for ncz, at in ((self.solid, 0x4000 + 16 + 0x80 + 40), (self.block, -1), (self.block, 0x4000 + 16 + 0x80 + 24 + 100)):
            bad = bytearray(ncz); bad[at] ^= 1
            with self.assertRaises(pipeline.BuildError): self.dumps.decompress(self.nsz(bytes(bad)), self.d / 'out.nsp')
            self.assertFalse((self.d / 'out.nsp').exists())

    def test_scan_takes_compressed_dumps(self):   # upstream's scan_nsps with porter's kinds: the NSZ's ticket names its title ID
        self.nsz(self.solid, 'Game [v0].nsz')
        app, fam = pipeline.nsp.scan_nsps(self.d, self.dumps.EXTS)
        self.assertEqual(app, 0x0100000000AAA000); self.assertEqual(fam['base'].suffix, '.nsz')


@unittest.skipUnless(importlib.util.find_spec('Crypto'), 'no pycryptodome')
class UntaggedXci(unittest.TestCase):   # issue #28: a cartridge dump named by its release ("hr-bdlwa.xci") has no ticket and no [title ID]: the content meta NCA's header names it
    BASE = 0x01009D901BC56000

    def setUp(self):
        rnd = random.Random(28); self.hk = bytes(rnd.getrandbits(8) for _ in range(32))
        self.d = Path(tempfile.mkdtemp(prefix='nx2a_test_xci_'))
        self.keys = self.d / 'prod.keys'; self.keys.write_text(f'header_key = {self.hk.hex()}\n')

    def nca(self, tid, ctype):   # an NCA header (0xc00 bytes) whose second sector is encrypted as Nintendo does: AES-XTS, the sector number big-endian as the tweak
        from Crypto.Cipher import AES
        p = bytearray(0x200); p[:4] = b'NCA3'; p[5] = ctype; struct.pack_into('<Q', p, 0x10, tid)
        data, t = AES.new(self.hk[:16], AES.MODE_ECB), int.from_bytes(AES.new(self.hk[16:], AES.MODE_ECB).encrypt((1).to_bytes(16, 'big')), 'little')
        c = b''
        for i in range(0, 0x200, 16):
            tb = t.to_bytes(16, 'little'); c += bytes(a ^ b for a, b in zip(data.encrypt(bytes(a ^ b for a, b in zip(p[i:i + 16], tb))), tb))
            t = (t << 1 & (1 << 128) - 1) ^ (0x87 if t >> 127 else 0)
        return bytes(0x200) + c + bytes(0x800)

    def xci(self, name, ncas, key_area=False):
        root = _hfs0([('update', _hfs0([])), ('secure', _hfs0(ncas))])
        x = bytearray(0x200); x[0x100:0x104] = b'HEAD'; x[0x130:0x138] = struct.pack('<Q', 0x200)
        (self.d / name).write_bytes(bytes(0x1000 if key_area else 0) + bytes(x) + root)

    def test_scan_reads_the_meta_nca(self):   # an update's program NCA carries the base game's title ID: only the meta NCA's counts, and the smallest of them is the base game
        self.xci('hr-bdlwa.xci', [('a.nca', self.nca(self.BASE + 0x800, 0)), ('b.cnmt.nca', self.nca(self.BASE + 0x800, 1)), ('c.nca', self.nca(self.BASE, 0)), ('d.cnmt.nca', self.nca(self.BASE, 1))])
        app, fam = pipeline.nsp.scan_nsps(self.d, ('nsp', 'xci'), self.keys)
        self.assertEqual(app, self.BASE); self.assertEqual(fam['base'].name, 'hr-bdlwa.xci')
        self.xci('hr-bdlwa.xci', [('c.nca', self.nca(self.BASE + 0x800, 0)), ('d.cnmt.nca', self.nca(self.BASE + 0x800, 1))], key_area=True)
        self.assertEqual(pipeline.nsp.title_of(self.d / 'hr-bdlwa.xci', self.hk), self.BASE + 0x800)

    def test_without_keys_the_error_says_how(self):
        self.xci('hr-bdlwa.xci', [('d.cnmt.nca', self.nca(self.BASE, 1))])
        with self.assertRaisesRegex(pipeline.BuildError, r'hr-bdlwa\.xci: no ticket .*no prod\.keys.*\[01009D901BC56000\]\[v0\]'): pipeline.nsp.scan_nsps(self.d, ('nsp', 'xci'))
        self.keys.write_text(f'header_key = {bytes(32).hex()}\n')   # another key: the header does not decrypt to NCA3
        with self.assertRaisesRegex(pipeline.BuildError, 'no content meta NCA'): pipeline.nsp.scan_nsps(self.d, ('nsp', 'xci'), self.keys)


class FpsLocker(unittest.TestCase):   # porter/fpslocker.py: FPSLocker-Warehouse configs → the host's rows (hle_fpslock.c DM_FPS_LOCKS) and the rewriter's cuts
    BID = 'd49165c05ead8ae140f189aa97934f39'
    UNITY = ('ALL_FPS:\n  # Vsync\n  -\n    type: evaluate_write\n    address: [MAIN, 0x7A642D8]\n    value_type: int32\n    value: VSYNC_TARGET\n'
             '  -\n    type: evaluate_write\n    address: [MAIN, 0x7A642E4]\n    value_type: float\n    value: "1 / FPS_LOCK_TARGET"\n')   # the shape of Viractal 1.4.5's config

    def rows(self, text, image=b''):
        from porter import fpslocker
        tsv, cuts = fpslocker.convert(text, image, self.BID, 'test')
        return [l.split('\t') for l in tsv.splitlines() if not l.startswith('#')], cuts

    def test_unity_vsync_and_frame_time(self):   # 2 vsyncs / 1/30 s at 30; 1 vsync / 1/62 s at 60 (FPS_LOCK_TARGET is the target + 2 at the refresh rate)
        rows, cuts = self.rows(self.UNITY)
        self.assertEqual(rows[0], ['build', self.BID]); self.assertEqual(cuts, [])
        self.assertEqual(rows[1][1:], ['int32', '0x7a642d8', '2', '1', '-'])
        self.assertEqual(rows[2][1:3], ['float', '0x7a642e4']); self.assertAlmostEqual(float(rows[2][3]), 1 / 30); self.assertAlmostEqual(float(rows[2][4]), 1 / 62)

    def test_expressions(self):
        from porter.fpslocker import evaluate
        self.assertEqual(evaluate('TruncDec(FRAMETIME_TARGET, 2)', 30), 33.33); self.assertEqual(evaluate('2^3', 60), 8)
        self.assertEqual(evaluate('VSYNC_TARGET', 60), 1); self.assertEqual(evaluate('floor(FPS_TARGET / 7)', 60), 8)
        with self.assertRaises(Exception): evaluate('__import__("os")', 60)

    def test_pointers_lists_and_compares(self):
        rows, _ = self.rows('ALL_FPS:\n  -\n    type: compare\n    compare_address: [MAIN, 0x10, 0x8]\n    compare_type: "!="\n    compare_value_type: int8\n    compare_value: 0\n'
                            '    address: [MAIN, 0x20, 0x30, -4]\n    value_type: int32\n    value: [2, 2]\n  -\n    type: block\n    what: timing\n')
        self.assertEqual(rows[1][1:], ['int32', '0x20,0x30,-0x4', '2,2', '2,2', 'int8 0x10,0x8 != 0'])
        self.assertEqual(len(rows), 2)   # block timing is FPSLocker's own frame delay: dropped

    def test_refused_whole(self):   # entries work together: one the host cannot apply refuses the config
        from porter import fpslocker
        for text in (self.UNITY + 'DECLARATIONS:\n  -\n    type: variable\n    name: x\n    value_type: float\n    default_value: 0\n',
                     self.UNITY + 'MASTER_WRITE:\n  -\n    type: asm_a64\n    main_offset: 0x10\n    instructions: [[b.ne, :goto1], [nop], :goto1, [nop]]\n',   # labels parse
                     'ALL_FPS:\n  -\n    type: write\n    address: [HEAP, 0x10]\n    value_type: int32\n    value: 1\n',
                     'ALL_FPS:\n  -\n    type: compare\n    compare_address: [MAIN, 0x10]\n    compare_type: "=="\n    compare_value_type: int8\n    compare_value: 1\n    value_type: refresh_rate\n    value: 30\n',
                     'ALL_FPS: [\n'):
            with self.assertRaises(fpslocker.Refused): fpslocker.convert(text, b'', self.BID)

    def test_master_write_bytes_are_cuts(self):   # the original bytes come from main's image, so the rewriter can assert them
        image = bytes(range(256)) * 4
        _, cuts = self.rows(self.UNITY + 'MASTER_WRITE:\n  -\n    type: bytes\n    main_offset: 0x10\n    value_type: uint8\n    value: [0x1F, 0x20, 0x03]\n', image)
        self.assertEqual(cuts, [(0x10, '101112', '1f2003', 'FPSLocker-Warehouse MASTER_WRITE entry 1')])
        from porter import fpslocker
        ns = {}; exec(fpslocker.patches_py(self.BID, cuts), ns); self.assertEqual(ns['CUTS'][self.BID][0][:3], (0x10, '101112', '1f2003'))

    def test_url(self):
        from porter import fpslocker
        self.assertTrue(fpslocker.url('0100fd501f95a000', self.BID).endswith('/patches/0100FD501F95A000/D49165C05EAD8AE1.yaml'))


if __name__ == '__main__':
    unittest.main()