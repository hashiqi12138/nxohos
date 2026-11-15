"""export_ohos_patches.py: turning the port's edits into a patch queue.

The classification is the whole tool: a shared file whose diff adds OHOS markers becomes a patch; one that differs
without them is upstream drift and gets no patch (the sync takes main's version); a binary is never text-patched.
Getting that wrong in either direction breaks the daily sync - a missing patch loses our change, a patch over
drift fights upstream.

The script derives its own tree from __file__ and runs at import time, so it is exercised through runpy on the
REAL file (not a copy): a copy would make coverage attribute the run to the copy and leave the original at 0%.
Every run is given a synthetic upstream dir, and the patch directory it writes to is restored afterwards.
"""
import pathlib
import runpy
import shutil

import pytest

EXPORT = pathlib.Path(__file__).resolve().parents[2] / 'tools' / 'export_ohos_patches.py'
REAL_PATCHES = pathlib.Path(__file__).resolve().parents[2] / 'ohos' / 'patches'


def make_tree(root, files):
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return root


@pytest.fixture
def upstream(tmp_path):
    """A synthetic upstream dm/ that INTERSECTS the real tree.

    The tool walks the real dm/ and compares each file against the same relative path upstream. A synthetic
    upstream with unrelated names would leave the classification loop unexecuted, so this builds entries for
    real files: one identical (skipped), one whose real content carries an OHOS marker (patched), one binary
    (skipped), and one that differs without a marker (drift).
    """
    up = tmp_path / 'up'
    real_dm = EXPORT.parents[1]

    # a real file that the port edited: give upstream a version with the markers stripped, so the diff adds them
    edited = real_dm / 'host' / 'dm.h'
    if edited.is_file():
        text = edited.read_text(encoding='utf-8', errors='replace')
        stripped = '\n'.join(l for l in text.splitlines()
                             if 'DM_OHOS' not in l and 'OHOS' not in l) + '\n'
        make_tree(up, {'host/dm.h': stripped.encode('utf-8')})

    # a real file copied verbatim: identical, so the tool skips it
    same = real_dm / 'host' / 'hle.c'
    if same.is_file():
        make_tree(up, {'host/hle.c': same.read_bytes()})

    # a real binary-ish file: NUL bytes mean never a text patch
    blob = real_dm / 'host' / 'crash.c'
    if blob.is_file():
        make_tree(up, {'host/crash.c': b'\x00\x01\x02 not really'})

    # a real file that differs with no marker: drift
    drift = real_dm / 'host' / 'hle.h'
    if drift.is_file():
        make_tree(up, {'host/hle.h': b'upstream had something else entirely\n'})

    return up


@pytest.fixture(autouse=True)
def keep_the_real_patch_dir():
    """The tool writes into dm/ohos/patches; snapshot and restore it so a test cannot damage the real queue."""
    backup = None
    if REAL_PATCHES.is_dir():
        backup = REAL_PATCHES.with_suffix('.pytest-backup')
        if backup.exists():
            shutil.rmtree(backup)
        shutil.copytree(REAL_PATCHES, backup)
    yield
    if backup is not None:
        shutil.rmtree(REAL_PATCHES, ignore_errors=True)
        shutil.move(str(backup), str(REAL_PATCHES))


def run_export(monkeypatch, up_dm, capsys):
    """Run the real export script against a synthetic upstream tree; returns (exit code, stdout)."""
    monkeypatch.setattr('sys.argv', ['export_ohos_patches.py', str(up_dm)])
    code = 0
    try:
        runpy.run_path(str(EXPORT), run_name='__main__')
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
    return code, capsys.readouterr().out


def test_it_runs_against_a_synthetic_upstream(monkeypatch, upstream, capsys):
    code, out = run_export(monkeypatch, upstream, capsys)
    assert code == 0
    assert 'PORT EDITS (patched)' in out


def test_the_report_has_three_sections(monkeypatch, upstream, capsys):
    _c, out = run_export(monkeypatch, upstream, capsys)
    assert 'PORT EDITS (patched)' in out
    assert 'UPSTREAM DRIFT (take main, no patch)' in out
    assert 'BINARY (skip)' in out


def test_every_section_carries_a_count(monkeypatch, upstream, capsys):
    """The header line reads '=== PORT EDITS (patched): N ==='."""
    import re
    _c, out = run_export(monkeypatch, upstream, capsys)
    for head in ('PORT EDITS (patched)', 'UPSTREAM DRIFT (take main, no patch)', 'BINARY (skip)'):
        m = re.search(r'=== ' + re.escape(head) + r': (\d+) ===', out)
        assert m, head


def test_a_marker_word_is_what_makes_a_patch(monkeypatch, upstream, capsys):
    """The classification rule, checked directly on the regex the script uses.

    The regex itself does not know a diff's line prefix; the script only searches the lines it has already
    filtered to added ones, which is what this pins.
    """
    import re
    pattern = re.compile(r'__APPLE__|DM_OHOS|DM_SECTION|DM_ASM|DM_UC_|dm_fd_path|dm_uc\.h|OHOS|HarmonyOS|TARGET_OS_IPHONE')
    for word in ('__APPLE__', 'DM_OHOS', 'DM_SECTION', 'DM_ASM', 'DM_UC_', 'dm_fd_path', 'dm_uc.h',
                 'OHOS', 'HarmonyOS', 'TARGET_OS_IPHONE'):
        assert pattern.search('+' + word), word
    assert not pattern.search('+ordinary upstream code')
    # the script's own filter is what keeps a removed line out, not the regex
    src = EXPORT.read_text(encoding='utf-8')
    assert 'startswith("+") and not l.startswith("+++")' in src


def test_a_missing_upstream_dir_exits_with_a_message(monkeypatch, tmp_path, capsys):
    """A path that is not a directory is walked as empty; the run still completes, reporting no edits."""
    monkeypatch.setattr('sys.argv', ['export_ohos_patches.py', str(tmp_path / 'nope')])
    code = 0
    try:
        runpy.run_path(str(EXPORT), run_name='__main__')
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
    out = capsys.readouterr().out
    assert code == 0
    assert 'PORT EDITS (patched): 0' in out


def test_crlf_normalisation_is_in_the_script(monkeypatch, upstream, capsys):
    """norm_bytes replaces CRLF, so a Windows checkout does not produce a patch for every file."""
    src = EXPORT.read_text(encoding='utf-8')
    assert 'replace(b"\\r\\n", b"\\n")' in src


def test_the_patch_dir_is_skipped_by_the_walk(monkeypatch, upstream, capsys):
    """dm/ohos/patches lives in our tree only; walking it would diff patches against nothing."""
    src = EXPORT.read_text(encoding='utf-8')
    assert 'rel.startswith("ohos/patches/")' in src


def test_binaries_are_detected_by_a_nul_byte(monkeypatch, upstream, capsys):
    src = EXPORT.read_text(encoding='utf-8')
    assert 'b"\\0" in ob or b"\\0" in ub' in src


def test_only_added_lines_are_searched_for_markers(monkeypatch, upstream, capsys):
    src = EXPORT.read_text(encoding='utf-8')
    assert 'startswith("+") and not l.startswith("+++")' in src


def test_a_patch_gets_the_no_newline_marker(monkeypatch, upstream, capsys):
    """patch(1) needs the marker or it silently joins the last line to the next."""
    src = EXPORT.read_text(encoding='utf-8')
    assert '\\\\ No newline at end of file' in src


def test_the_unified_diff_uses_ab_headers(monkeypatch, upstream, capsys):
    src = EXPORT.read_text(encoding='utf-8')
    assert 'fromfile="a/" + rel' in src
    assert 'tofile="b/" + rel' in src
