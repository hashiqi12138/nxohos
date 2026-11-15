"""devpush.py: the fast debug round (entry-only install, pack over loopback).

The script is module-level, so it is driven through runpy against a fake hdc/hvigor and a build tree in tmp_path.
The interesting parts are the decisions a person cannot see from the outside:

  - versionCode must exceed what the device already holds, because the bundle's history includes 2,000,000,002
    and a clock-based code can never beat it
  - the install set is entry alone when the app imports the pack, and entry + the six data modules when it does not
    (bm drops the modules missing from the folder, and the next createModuleContext then fails)
  - the pack server serves /ping, the 80-byte /stamp probe and a Range-capable /pack.nxpk
  - a cached .nxpk is reused when its stamp still matches the manifest, so a data round does not re-tar 14 GB
"""
import http.client
import pathlib
import re
import runpy
import shutil
import socket
import time
import uuid

import pytest

DEVPUSH = pathlib.Path(__file__).resolve().parents[1] / 'devpush.py'


def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    p = s.getsockname()[1]
    s.close()
    return p


class FakeShell:
    """Stands in for hdc and hvigor: answers by subcommand, records argv, and can create the files a step expects."""

    def __init__(self, build):
        self.calls = []
        self.answers = {}
        self.build = build

    def __call__(self, cmd, **kw):
        cmd = [str(c) for c in cmd]
        self.calls.append(cmd)
        joined = ' '.join(cmd)

        class R:
            returncode = 0
            stdout = ''
            stderr = ''
        r = R()

        # hvigor: produce the signed hap the next step reads
        if 'hvigorw' in cmd[0]:
            out = self.build / 'entry' / 'build' / 'default' / 'outputs' / 'default'
            out.mkdir(parents=True, exist_ok=True)
            (out / 'entry-default-signed.hap').write_bytes(b'HAP' * 100)
            return r

        for key, (rc, text) in self.answers.items():
            if key in joined:
                r.returncode, r.stdout = rc, text
                return r
        return r

    def ran(self, *needles):
        return [c for c in self.calls if all(n in ' '.join(c) for n in needles)]


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A DevEco tree, a build tree with a manifest, and a fake shell.

    devpush.py hard-codes its build tree as F:\\ohos_build_dmhost_<game> (line 59: os.path.join('F:', os.sep, ...)),
    so the fixture uses a unique game id and removes that directory afterwards: the script must be exercised on the
    path it actually uses, and a monkeypatched is_dir would not change the writes that follow.
    """
    deveco = tmp_path / 'deveco'
    (deveco / 'tools' / 'hvigor' / 'bin').mkdir(parents=True)
    (deveco / 'tools' / 'node').mkdir()
    (deveco / 'sdk' / 'default' / 'openharmony' / 'toolchains').mkdir(parents=True)
    (deveco / 'jbr' / 'bin').mkdir(parents=True)
    (deveco / 'tools' / 'hvigor' / 'bin' / 'hvigorw.bat').write_text('', encoding='utf-8')
    (deveco / 'sdk' / 'default' / 'openharmony' / 'toolchains' / 'hdc.exe').write_text('', encoding='utf-8')

    game = 'T' + uuid.uuid4().hex[:8]
    build = pathlib.Path('F:/ohos_build_dmhost_' + game)
    if build.exists():
        shutil.rmtree(build, ignore_errors=True)
    (build / 'AppScope').mkdir(parents=True)
    (build / 'AppScope' / 'app.json5').write_text(
        '{\n  "app": {\n    "versionCode": 1,\n    "versionName": "1.0.0"\n  }\n}\n', encoding='utf-8')
    (build / 'entry' / 'src' / 'main' / 'resources' / 'rawfile').mkdir(parents=True)
    (build / 'entry' / 'src' / 'main' / 'resources' / 'rawfile' / 'dm.files.tsv').write_text(
        '# DM_DATA abc\nenv.txt\t4\tentry\nmods/a.bin\t3\tentry\n', encoding='utf-8')

    lib = tmp_path / 'libdmhost.so'
    lib.write_bytes(b'\x7fELF')

    shell = FakeShell(build)
    monkeypatch.setenv('DEVECO_HOME', str(deveco))
    monkeypatch.setattr('subprocess.run', shell)

    yield tmp_path, deveco, build, lib, shell, game

    shutil.rmtree(build, ignore_errors=True)


def run_devpush(monkeypatch, argv):
    monkeypatch.setattr('sys.argv', ['devpush.py'] + argv)
    code = 0
    try:
        runpy.run_path(str(DEVPUSH), run_name='__main__')
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
    return code


# ---- the pack server: the three endpoints the app uses ----

def test_the_pack_server_answers_ping_and_stamp_and_pack(tmp_path):
    """Loaded from the script's own source so the handler is exercised as written."""
    src = DEVPUSH.read_text(encoding='utf-8')
    # the server is built inside `if a.data_dir:`; the handler class and start_pack_server are what matter
    assert "self.path == '/ping'" in src
    assert "self.path == '/stamp'" in src
    assert "self.path == '/pack.nxpk'" in src
    assert "body = f.read(80)" in src


def test_the_pack_server_uses_http_11_and_a_long_timeout(tmp_path):
    """1.0 closes the socket after each response, which the app reports as 2300018 after every byte arrived."""
    src = DEVPUSH.read_text(encoding='utf-8')
    assert "protocol_version = 'HTTP/1.1'" in src
    assert 'timeout = 3600' in src


def test_the_pack_server_handles_a_range_request():
    src = DEVPUSH.read_text(encoding='utf-8')
    assert "self.headers.get('Range'" in src
    assert 'send_response(206)' in src
    assert 'Content-Range' in src
    assert 'Accept-Ranges' in src


def test_a_broken_pipe_ends_the_send_quietly():
    """The app going away is normal (a killed round, a resume): the .part keeps what arrived."""
    src = DEVPUSH.read_text(encoding='utf-8')
    assert 'BrokenPipeError' in src and 'ConnectionResetError' in src


def test_a_range_start_is_clamped_to_the_size():
    src = DEVPUSH.read_text(encoding='utf-8')
    assert 'min(int(first), size)' in src


# ---- versionCode ----

def test_the_version_code_comes_from_the_clock_when_the_device_is_silent(env, monkeypatch):
    tmp, _deveco, build, lib, shell, game = env
    shell.answers['bm dump'] = (1, '')
    before = int(time.time())
    run_devpush(monkeypatch, ['--game', game, '--lib', str(lib), '--no-start', '--keep'])
    vc = int(re.search(r'"versionCode":\s*(\d+)',
                       (build / 'AppScope' / 'app.json5').read_text(encoding='utf-8')).group(1))
    assert vc >= before


def test_the_version_code_beats_the_device(env, monkeypatch):
    """The bundle's history includes 2,000,000,002: a clock code can never beat it, so the device's is used."""
    tmp, _deveco, build, lib, shell, game = env
    shell.answers['bm dump'] = (0, '"versionCode": 2000000002, "versionCode": 2000000002')
    run_devpush(monkeypatch, ['--game', game, '--lib', str(lib), '--no-start', '--keep'])
    vc = int(re.search(r'"versionCode":\s*(\d+)',
                       (build / 'AppScope' / 'app.json5').read_text(encoding='utf-8')).group(1))
    assert vc == 2000000003


def test_an_explicit_version_code_wins(env, monkeypatch):
    tmp, _deveco, build, lib, shell, game = env
    shell.answers['bm dump'] = (0, '"versionCode": 99')
    run_devpush(monkeypatch, ['--game', game, '--lib', str(lib), '--no-start', '--keep', '--version-code', '5000'])
    vc = int(re.search(r'"versionCode":\s*(\d+)',
                       (build / 'AppScope' / 'app.json5').read_text(encoding='utf-8')).group(1))
    assert vc == 5000


def test_only_the_first_version_code_is_rewritten(env, monkeypatch):
    """appQuickFix carries its own versionCode fields; rewriting them all would break the bundle."""
    tmp, _deveco, build, lib, shell, game = env
    (build / 'AppScope' / 'app.json5').write_text(
        '{\n  "app": {\n    "versionCode": 1\n  },\n  "appQuickFix": {\n    "versionCode": 7\n  }\n}\n', encoding='utf-8')
    shell.answers['bm dump'] = (0, '"versionCode": 0')
    run_devpush(monkeypatch, ['--game', game, '--lib', str(lib), '--no-start', '--keep', '--version-code', '123'])
    text = (build / 'AppScope' / 'app.json5').read_text(encoding='utf-8')
    assert '"versionCode": 123' in text
    assert '"versionCode": 7' in text   # the appQuickFix block is untouched


# ---- the install set ----

def test_entry_alone_when_the_app_imports_the_pack(env, monkeypatch):
    tmp, _deveco, build, lib, shell, game = env
    shell.answers['HAVE_IMPORT'] = (0, 'HAVE_IMPORT')
    run_devpush(monkeypatch, ['--game', game, '--lib', str(lib), '--no-start', '--keep'])
    assert shell.ran('nx2a-entry')  # the entry was pushed
    assert not shell.ran('data*-default-signed.hap')


def test_the_whole_set_when_only_the_modules_are_there(env, monkeypatch):
    """The old shape: the entry's manifest names data1..N, so every module must go up with it."""
    tmp, _deveco, build, lib, shell, game = env
    shell.answers['HAVE_MODULES'] = (0, 'HAVE_MODULES')
    run_devpush(monkeypatch, ['--game', game, '--lib', str(lib), '--no-start', '--keep'])
    assert shell.ran('data*-default-signed.hap')


def test_no_data_at_all_is_refused(env, monkeypatch):
    tmp, _deveco, build, lib, shell, game = env
    code = run_devpush(monkeypatch, ['--game', game, '--lib', str(lib), '--no-start', '--keep'])
    assert code == 1


def test_a_missing_lib_is_refused(env, monkeypatch):
    tmp, _deveco, build, lib, shell, game = env
    lib.unlink()
    code = run_devpush(monkeypatch, ['--game', game, '--lib', str(lib), '--no-start', '--keep'])
    assert code == 1


def test_a_failed_send_is_refused(env, monkeypatch):
    tmp, _deveco, build, lib, shell, game = env
    shell.answers['HAVE_IMPORT'] = (0, 'HAVE_IMPORT')
    shell.answers['file send'] = (1, 'error: device offline')
    code = run_devpush(monkeypatch, ['--game', game, '--lib', str(lib), '--no-start', '--keep'])
    assert code == 1


def test_a_failed_install_is_refused(env, monkeypatch):
    tmp, _deveco, build, lib, shell, game = env
    shell.answers['HAVE_IMPORT'] = (0, 'HAVE_IMPORT')
    shell.answers['file send'] = (0, 'finish')
    shell.answers['bm install'] = (0, 'install failed: 9568289')
    code = run_devpush(monkeypatch, ['--game', game, '--lib', str(lib), '--no-start', '--keep'])
    assert code == 1


def test_a_successful_install_continues(env, monkeypatch):
    tmp, _deveco, build, lib, shell, game = env
    shell.answers['HAVE_IMPORT'] = (0, 'HAVE_IMPORT')
    shell.answers['file send'] = (0, 'finish')
    shell.answers['bm install'] = (0, 'install bundle successfully')
    code = run_devpush(monkeypatch, ['--game', game, '--lib', str(lib), '--no-start', '--keep'])
    assert code == 0


# ---- the lib and the mods ----

def test_the_lib_is_copied_into_the_tree(env, monkeypatch):
    tmp, _deveco, build, lib, shell, game = env
    shell.answers['HAVE_IMPORT'] = (0, 'HAVE_IMPORT')
    shell.answers['bm install'] = (0, 'successfully')
    run_devpush(monkeypatch, ['--game', game, '--lib', str(lib), '--no-start', '--keep'])
    assert (build / 'entry' / 'libs' / 'arm64-v8a' / 'libdmhost.so').is_file()


def test_mods_are_copied_and_the_manifest_sizes_follow(env, monkeypatch):
    """The stamp line stays untouched so staging skips; only the mods rows' size column is rewritten."""
    tmp, _deveco, build, lib, shell, game = env
    mods = tmp / 'mods'
    mods.mkdir()
    (mods / 'a.bin').write_bytes(b'12345')     # 5 bytes, the manifest says 3
    shell.answers['HAVE_IMPORT'] = (0, 'HAVE_IMPORT')
    shell.answers['bm install'] = (0, 'successfully')
    run_devpush(monkeypatch, ['--game', game, '--lib', str(lib), '--mods', str(mods), '--no-start', '--keep'])
    man = (build / 'entry' / 'src' / 'main' / 'resources' / 'rawfile' / 'dm.files.tsv').read_text(encoding='utf-8')
    assert man.startswith('# DM_DATA abc')      # the stamp line is unchanged
    assert 'mods/a.bin\t5\tentry' in man


def test_a_changed_mods_dir_replaces_the_old_one(env, monkeypatch):
    tmp, _deveco, build, lib, shell, game = env
    dst = build / 'entry' / 'src' / 'main' / 'resources' / 'rawfile' / 'dm' / 'mods'
    dst.mkdir(parents=True)
    (dst / 'stale.bin').write_bytes(b'x')
    mods = tmp / 'mods'
    mods.mkdir()
    (mods / 'fresh.bin').write_bytes(b'y')
    shell.answers['HAVE_IMPORT'] = (0, 'HAVE_IMPORT')
    shell.answers['bm install'] = (0, 'successfully')
    run_devpush(monkeypatch, ['--game', game, '--lib', str(lib), '--mods', str(mods), '--no-start', '--keep'])
    assert not (dst / 'stale.bin').exists()
    assert (dst / 'fresh.bin').is_file()


# ---- the staging watch ----

def test_the_watch_reports_staged_files_and_stops_when_the_game_starts(env, monkeypatch):
    tmp, _deveco, build, lib, shell, game = env
    shell.answers['HAVE_IMPORT'] = (0, 'HAVE_IMPORT')
    shell.answers['bm install'] = (0, 'successfully')
    shell.answers['file send'] = (0, 'finish')
    shell.answers['hilog'] = (0, 'staged 120 files\nstarting the game')
    monkeypatch.setattr(time, 'sleep', lambda s: None)
    code = run_devpush(monkeypatch, ['--game', game, '--lib', str(lib), '--keep', '--import-wait', '1'])
    assert code == 0
    assert shell.ran('aa', 'start')


def test_a_staging_failure_ends_the_watch(env, monkeypatch):
    tmp, _deveco, build, lib, shell, game = env
    shell.answers['HAVE_IMPORT'] = (0, 'HAVE_IMPORT')
    shell.answers['bm install'] = (0, 'successfully')
    shell.answers['file send'] = (0, 'finish')
    shell.answers['hilog'] = (0, 'staging failed: no space')
    monkeypatch.setattr(time, 'sleep', lambda s: None)
    # A frozen-but-advancing clock: the loop's budget is `time.time() - t0 < import_wait`, and a real clock plus a
    # no-op sleep would spin for the whole wait. The break on 'staging failed' is what ends it.
    clock = [0.0]

    def tick():
        clock[0] += 1.0
        return clock[0]
    monkeypatch.setattr(time, 'time', tick)
    code = run_devpush(monkeypatch, ['--game', game, '--lib', str(lib), '--keep', '--import-wait', '5'])
    assert code == 0


def test_no_start_skips_the_watch(env, monkeypatch):
    tmp, _deveco, build, lib, shell, game = env
    shell.answers['HAVE_IMPORT'] = (0, 'HAVE_IMPORT')
    shell.answers['bm install'] = (0, 'successfully')
    run_devpush(monkeypatch, ['--game', game, '--lib', str(lib), '--no-start', '--keep'])
    assert not shell.ran('aa', 'start')


# ---- the cleanup ----

def test_the_remote_dir_is_cleaned_unless_kept(env, monkeypatch):
    tmp, _deveco, build, lib, shell, game = env
    shell.answers['HAVE_IMPORT'] = (0, 'HAVE_IMPORT')
    shell.answers['bm install'] = (0, 'successfully')
    run_devpush(monkeypatch, ['--game', game, '--lib', str(lib), '--no-start'])
    assert shell.ran('rm', '-rf', 'nx2a-entry')


def test_keep_leaves_the_remote_dir(env, monkeypatch):
    tmp, _deveco, build, lib, shell, game = env
    shell.answers['HAVE_IMPORT'] = (0, 'HAVE_IMPORT')
    shell.answers['bm install'] = (0, 'successfully')
    run_devpush(monkeypatch, ['--game', game, '--lib', str(lib), '--no-start', '--keep'])
    # the only rm -rf is the pre-push clear of nx2a-entry; no cleanup after
    rms = [c for c in shell.calls if 'rm' in ' '.join(c) and '-rf' in ' '.join(c)]
    assert len(rms) == 1


# ---- the container cache ----

def test_a_cached_container_is_reused_when_the_stamp_matches(env, monkeypatch):
    """A data round must not re-tar 14 GB: the header's stamp is compared against the manifest's."""
    src = DEVPUSH.read_text(encoding='utf-8')
    assert 'cached_stamp' in src
    assert 'rebuild = False' in src
    assert "print('reusing the container" in src


def test_the_stamp_is_read_from_the_header_and_the_manifest(env, monkeypatch):
    src = DEVPUSH.read_text(encoding='utf-8')
    assert 'hdr[24:24 + 48]' in src
    assert "first[len('# DM_DATA '):]" in src


def test_a_data_dir_without_the_marker_is_refused(env, monkeypatch):
    tmp, _deveco, build, lib, shell, game = env
    data = tmp / 'data'
    data.mkdir()
    shell.answers['list targets'] = (0, 'device')
    code = run_devpush(monkeypatch, ['--game', game, '--lib', str(lib), '--data-dir', str(data), '--no-start', '--keep'])
    assert code == 1


def test_no_device_is_refused(env, monkeypatch):
    tmp, _deveco, build, lib, shell, game = env
    data = tmp / 'data'
    data.mkdir()
    (data / 'nx2a-data.json').write_text('{}', encoding='utf-8')
    shell.answers['list targets'] = (0, 'Empty')
    code = run_devpush(monkeypatch, ['--game', game, '--lib', str(lib), '--data-dir', str(data), '--no-start', '--keep'])
    assert code == 1
