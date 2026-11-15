"""mkhap.py: the HAP builder around libdmhost.so.

The script is module-level code, so it is driven through runpy with a synthetic project tree. hvigor and hdc are
replaced by a recorder: the tests care about what the builder DECIDED (which module carries which file, the
manifest's stamp and module column, the split arithmetic, the refusals), not about a real build.

The refusals are the interesting half: they are what turns a 15 GB pack that cannot fit into a sentence a person
can act on instead of a failed signer run ten minutes later.
"""
import json
import os
import pathlib
import runpy
import shutil

import pytest

MKHAP = pathlib.Path(__file__).resolve().parents[1] / 'mkhap.py'


def make_project(root):
    """A HAP project skeleton: AppScope/app.json5 plus an entry module."""
    (root / 'AppScope').mkdir(parents=True)
    (root / 'AppScope' / 'app.json5').write_text(
        '{\n  "app": {\n    "versionCode": 1,\n    "versionName": "1.0.0"\n  }\n}\n', encoding='utf-8')
    (root / 'entry' / 'src' / 'main').mkdir(parents=True)
    (root / 'build-profile.json5').write_text(
        json.dumps({'app': {'products': [{'name': 'default'}]}, 'modules': [{'name': 'entry', 'srcPath': './entry'}]}),
        encoding='utf-8')
    return root


def make_data(root, files):
    for rel, size in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b'x' * size)
    return root


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Every path the script derives, redirected into tmp_path, with a fake hvigor and hdc."""
    hap_src = make_project(tmp_path / 'hap')
    out = tmp_path / 'out'
    out.mkdir()
    game = '01007EF00011E000'
    (out / game).mkdir()
    (out / game / 'libdmhost.so').write_bytes(b'\x7fELF' + b'\x00' * 60)

    # a fake DevEco tree so find_deveco() answers
    deveco = tmp_path / 'deveco'
    (deveco / 'tools' / 'hvigor' / 'bin').mkdir(parents=True)
    (deveco / 'tools' / 'node').mkdir()
    (deveco / 'sdk' / 'default' / 'openharmony' / 'toolchains').mkdir(parents=True)
    (deveco / 'jbr' / 'bin').mkdir(parents=True)
    (deveco / 'tools' / 'hvigor' / 'bin' / 'hvigorw.bat').write_text('', encoding='utf-8')
    (deveco / 'sdk' / 'default' / 'openharmony' / 'toolchains' / 'hdc.exe').write_text('', encoding='utf-8')

    build = tmp_path / ('ohos_build_dmhost_' + game)
    monkeypatch.setenv('DEVECO_HOME', str(deveco))
    monkeypatch.setenv('DM_HAP_BUILD_DIR', str(build))
    monkeypatch.setenv('DM_VERSION_CODE', '12345')

    calls = []

    def fake_run(cmd, **kw):
        calls.append(list(cmd))

        class R:
            returncode = 0
        return R()

    monkeypatch.setattr('subprocess.run', fake_run)
    return tmp_path, hap_src, out, game, build, calls


def run_mkhap(monkeypatch, argv, sandbox=None):
    """Run the real script.

    HAP_SRC and OUT are derived from __file__, so the caller passes --lib and --out explicitly to keep every write
    in tmp_path. The project itself is the real dm/ohos/hap: it is small, it is what the builder is meant to copy,
    and patching shutil.copytree would recurse (shutil's own _copytree calls copytree from its namespace).
    """
    if sandbox is not None:
        _tmp, _hap_src, out, game, _build, _calls = sandbox
        argv = ['--lib', str(out / game / 'libdmhost.so'), '--out', str(out)] + argv
    monkeypatch.setattr('sys.argv', ['mkhap.py'] + argv)
    code = 0
    try:
        runpy.run_path(str(MKHAP), run_name='__main__')
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
    return code


# ---- the refusal before anything is copied ----

def test_a_missing_so_exits_before_copying(sandbox, monkeypatch):
    tmp, _src, out, game, _build, _calls = sandbox
    (out / game / 'libdmhost.so').unlink()
    code = run_mkhap(monkeypatch, ['--game', game], sandbox)
    assert code == 1


def test_a_missing_deveco_exits(sandbox, monkeypatch):
    _tmp, _src, _out, game, _build, _calls = sandbox
    monkeypatch.delenv('DEVECO_HOME')
    # find_deveco falls through to hard-coded paths; with none of them present it exits
    monkeypatch.setattr(os.path, 'isdir', lambda p: False)
    code = run_mkhap(monkeypatch, ['--game', game], sandbox)
    assert code == 1


def test_a_build_dir_that_is_not_ours_is_refused(sandbox, monkeypatch):
    """DM_HAP_BUILD_DIR pointed at a wrong path must not delete it."""
    tmp, _src, _out, game, _build, _calls = sandbox
    wrong = tmp / 'important-folder'
    wrong.mkdir()
    (wrong / 'keep.txt').write_text('do not delete', encoding='utf-8')
    monkeypatch.setenv('DM_HAP_BUILD_DIR', str(wrong))
    code = run_mkhap(monkeypatch, ['--game', game], sandbox)
    assert code == 1
    assert (wrong / 'keep.txt').is_file()


# ---- a build without --data: entry only ----

def test_a_plain_build_copies_the_project_and_the_so(sandbox, monkeypatch):
    _tmp, _src, _out, game, build, _calls = sandbox
    run_mkhap(monkeypatch, ['--game', game, '--keep-build'], sandbox)
    assert (build / 'AppScope' / 'app.json5').is_file()
    assert (build / 'entry' / 'libs' / 'arm64-v8a' / 'libdmhost.so').is_file()


def test_the_version_code_is_overridden(sandbox, monkeypatch):
    _tmp, _src, _out, game, build, _calls = sandbox
    run_mkhap(monkeypatch, ['--game', game, '--keep-build'], sandbox)
    text = (build / 'AppScope' / 'app.json5').read_text(encoding='utf-8')
    assert '"versionCode": 12345' in text


def test_hvigor_runs_once_without_data(sandbox, monkeypatch):
    _tmp, _src, _out, game, _build, calls = sandbox
    run_mkhap(monkeypatch, ['--game', game, '--keep-build'], sandbox)
    hv = [c for c in calls if 'hvigorw' in str(c[0])]
    assert len(hv) == 1
    assert '--mode' in hv[0] and 'module' in hv[0]


def test_the_pad_default_comes_from_the_profile(sandbox, monkeypatch):
    _tmp, _src, _out, game, build, _calls = sandbox
    run_mkhap(monkeypatch, ['--game', game, '--keep-build'], sandbox)
    p = build / 'entry' / 'src' / 'main' / 'resources' / 'rawfile' / 'pad_default.json'
    assert p.is_file()
    got = json.loads(p.read_text(encoding='utf-8'))
    assert got['layout'] in ('full', 'touch')
    assert isinstance(got['handheld'], bool)


# ---- a build with --data: the module split ----

def test_small_files_go_to_entry_and_bulk_is_split(sandbox, monkeypatch):
    tmp, _src, _out, game, build, _calls = sandbox
    data = make_data(tmp / 'data', {
        'env.txt': 10,
        'RomFS/big1.bin': 3 * 2**20,
        'RomFS/big2.bin': 3 * 2**20,
    })
    run_mkhap(monkeypatch, ['--game', game, '--data', str(data), '--split-mb', '4', '--keep-build'], sandbox)
    # env.txt (small) is in entry; the two 3 MB RomFS files exceed the 4 MB part limit together
    assert (build / 'entry' / 'src' / 'main' / 'resources' / 'rawfile' / 'dm' / 'env.txt').is_file()
    assert (build / 'data1' / 'src' / 'main' / 'resources' / 'rawfile' / 'dm' / 'RomFS' / 'big1.bin').is_file()


def test_the_manifest_carries_a_stamp_and_the_module_column(sandbox, monkeypatch):
    tmp, _src, _out, game, build, _calls = sandbox
    data = make_data(tmp / 'data', {'env.txt': 10, 'RomFS/a.bin': 3 * 2**20})
    run_mkhap(monkeypatch, ['--game', game, '--data', str(data), '--split-mb', '4', '--keep-build'], sandbox)
    man = build / 'entry' / 'src' / 'main' / 'resources' / 'rawfile' / 'dm.files.tsv'
    lines = man.read_text(encoding='utf-8').splitlines()
    assert lines[0].startswith('# DM_DATA ')
    assert len(lines[0][len('# DM_DATA '):]) == 40
    env = [l for l in lines[1:] if l.startswith('env.txt\t')]
    assert env and env[0].endswith('\tentry')


def test_each_module_gets_its_hvigorfile_and_metadata(sandbox, monkeypatch):
    tmp, _src, _out, game, build, _calls = sandbox
    data = make_data(tmp / 'data', {'RomFS/a.bin': 3 * 2**20, 'RomFS/b.bin': 3 * 2**20})
    run_mkhap(monkeypatch, ['--game', game, '--data', str(data), '--split-mb', '4', '--keep-build'], sandbox)
    d = build / 'data1'
    assert (d / 'hvigorfile.ts').is_file()
    assert (d / 'oh-package.json5').is_file()
    assert (d / 'build-profile.json5').is_file()
    assert (d / 'src' / 'main' / 'module.json5').is_file()
    mod = json.loads((d / 'src' / 'main' / 'module.json5').read_text(encoding='utf-8'))
    assert mod['module']['type'] == 'feature'
    assert mod['module']['name'] == 'data1'


def test_the_build_profile_lists_every_extra_module(sandbox, monkeypatch):
    tmp, _src, _out, game, build, _calls = sandbox
    data = make_data(tmp / 'data', {'RomFS/a.bin': 3 * 2**20, 'RomFS/b.bin': 3 * 2**20})
    run_mkhap(monkeypatch, ['--game', game, '--data', str(data), '--split-mb', '4', '--keep-build'], sandbox)
    cfg = json.loads((build / 'build-profile.json5').read_text(encoding='utf-8'))
    names = [m['name'] for m in cfg['modules']]
    assert 'entry' in names
    assert any(n.startswith('data') for n in names)


def test_one_hvigor_run_per_module(sandbox, monkeypatch):
    tmp, _src, _out, game, _build, calls = sandbox
    data = make_data(tmp / 'data', {'RomFS/a.bin': 3 * 2**20, 'RomFS/b.bin': 3 * 2**20})
    run_mkhap(monkeypatch, ['--game', game, '--data', str(data), '--split-mb', '4', '--keep-build'], sandbox)
    hv = [c for c in calls if 'hvigorw' in str(c[0])]
    assert len(hv) >= 2
    assert all('--mode' in c for c in hv)


def test_a_file_larger_than_the_split_limit_is_refused(sandbox, monkeypatch):
    """rawfile cannot split a file, so a single oversized one has nowhere to go."""
    tmp, _src, _out, game, _build, _calls = sandbox
    data = make_data(tmp / 'data', {'RomFS/huge.bin': 5 * 2**20})
    code = run_mkhap(monkeypatch, ['--game', game, '--data', str(data), '--split-mb', '4', '--keep-build'], sandbox)
    assert code == 1


def test_a_full_disk_is_refused_before_copying(sandbox, monkeypatch):
    tmp, _src, _out, game, _build, _calls = sandbox
    data = make_data(tmp / 'data', {'RomFS/a.bin': 1024})

    class Usage:
        free = 1024
    monkeypatch.setattr('shutil.disk_usage', lambda p: Usage())
    code = run_mkhap(monkeypatch, ['--game', game, '--data', str(data), '--keep-build'], sandbox)
    assert code == 1


def test_large_files_ride_with_the_bulk_not_entry(sandbox, monkeypatch):
    """shaders.dmsv is per-game data, not runtime: entry must stay small so a debug cycle replaces it alone."""
    tmp, _src, _out, game, build, _calls = sandbox
    data = make_data(tmp / 'data', {'shaders.dmsv': 9 * 2**20})
    run_mkhap(monkeypatch, ['--game', game, '--data', str(data), '--split-mb', '20', '--keep-build'], sandbox)
    entry = build / 'entry' / 'src' / 'main' / 'resources' / 'rawfile' / 'dm' / 'shaders.dmsv'
    assert not entry.is_file()


def test_aoc_rides_with_the_bulk(sandbox, monkeypatch):
    tmp, _src, _out, game, build, _calls = sandbox
    data = make_data(tmp / 'data', {'aoc/dlc.bin': 1024, 'env.txt': 10})
    run_mkhap(monkeypatch, ['--game', game, '--data', str(data), '--split-mb', '20', '--keep-build'], sandbox)
    assert (build / 'entry' / 'src' / 'main' / 'resources' / 'rawfile' / 'dm' / 'env.txt').is_file()
    # aoc is bulk: it must not land in entry
    assert not (build / 'entry' / 'src' / 'main' / 'resources' / 'rawfile' / 'dm' / 'aoc' / 'dlc.bin').is_file()


# ---- the output ----

def test_the_signed_hap_is_copied_out(sandbox, monkeypatch):
    tmp, _src, out, game, build, _calls = sandbox

    def fake_run(cmd, **kw):
        # pretend hvigor produced a signed hap
        p = build / 'entry' / 'build' / 'default' / 'outputs' / 'default'
        p.mkdir(parents=True, exist_ok=True)
        (p / 'entry-default-signed.hap').write_bytes(b'HAP')
        (p / 'entry-default-unsigned.hap').write_bytes(b'HAP2')

        class R:
            returncode = 0
        return R()
    monkeypatch.setattr('subprocess.run', fake_run)
    run_mkhap(monkeypatch, ['--game', game], sandbox)
    assert (out / 'dmhost-signed.hap').is_file()


def test_an_unsigned_hap_is_reported_and_fails(sandbox, monkeypatch):
    """Only an unsigned hap exists: it is copied out under a name that says so, and the run fails."""
    tmp, _src, out, game, build, _calls = sandbox

    def fake_run(cmd, **kw):
        p = build / 'entry' / 'build' / 'default' / 'outputs' / 'default'
        p.mkdir(parents=True, exist_ok=True)
        (p / 'entry-default-unsigned.hap').write_bytes(b'HAP2')

        class R:
            returncode = 0
        return R()
    monkeypatch.setattr('subprocess.run', fake_run)
    code = run_mkhap(monkeypatch, ['--game', game], sandbox)
    assert code == 1
    assert (out / 'dmhost-unsigned.hap').is_file()
    assert not (out / 'dmhost-signed.hap').is_file()


def test_no_hap_at_all_fails(sandbox, monkeypatch):
    _tmp, _src, _out, game, _build, _calls = sandbox
    code = run_mkhap(monkeypatch, ['--game', game], sandbox)
    assert code == 1


def test_the_build_dir_is_removed_unless_kept(sandbox, monkeypatch):
    """Cleanup runs after the hap is placed; the no-hap abort exits earlier and leaves the tree as evidence."""
    _tmp, _src, _out, game, build, _calls = sandbox

    def fake_run(cmd, **kw):
        p = build / 'entry' / 'build' / 'default' / 'outputs' / 'default'
        p.mkdir(parents=True, exist_ok=True)
        (p / 'entry-default-signed.hap').write_bytes(b'HAP')

        class R:
            returncode = 0
        return R()
    monkeypatch.setattr('subprocess.run', fake_run)
    run_mkhap(monkeypatch, ['--game', game], sandbox)
    assert not build.exists()


def test_the_no_hap_abort_keeps_the_tree(sandbox, monkeypatch):
    """Nothing was produced: the half-built tree stays, so the hvigor output can be read."""
    _tmp, _src, _out, game, build, _calls = sandbox
    run_mkhap(monkeypatch, ['--game', game], sandbox)
    assert build.exists()


def test_keep_build_leaves_the_tree(sandbox, monkeypatch):
    _tmp, _src, _out, game, build, _calls = sandbox
    run_mkhap(monkeypatch, ['--game', game, '--keep-build'], sandbox)
    assert build.is_dir()
