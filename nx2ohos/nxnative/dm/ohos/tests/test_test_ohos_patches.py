"""test_ohos_patches.py: the guard that keeps the port syncable with upstream.

The script runs at import time (it is a CLI, not a library), so every test drives it through runpy with a
synthetic --upstream-dir. That also means the tests build a whole miniature upstream tree and a patch queue, which
is what makes them worth having: a stale patch or a drift file is exactly the failure that would otherwise be
noticed only when the daily sync breaks.

The guard's contract:
  1. every patch in dm/ohos/patches applies to upstream main
  2. applying it reproduces the working file byte for byte (LF-normalised)
  3. the additive directories exist
  4. a shared file that differs from upstream with no patch is reported as drift, not as a failure
"""
import os
import pathlib
import runpy
import shutil

import pytest

GUARD = pathlib.Path(__file__).resolve().parents[2] / 'tools' / 'test_ohos_patches.py'


def make_tree(root, files):
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return root


def run_guard(monkeypatch, our_dm, upstream_dm):
    """Execute the guard with --upstream-dir, capturing its exit code."""
    monkeypatch.setattr('sys.argv', ['test_ohos_patches.py', '--upstream-dir', str(upstream_dm)])
    # OURS is derived from __file__, so the module must see OUR tree. Point it there by running the copy.
    monkeypatch.setattr(os.path, 'dirname', os.path.dirname)
    with pytest.raises(SystemExit) as e:
        runpy.run_path(str(our_dm / 'tools' / 'test_ohos_patches.py'), run_name='__main__')
    return e.value.code


@pytest.fixture
def trees(tmp_path):
    """A minimal port tree and upstream tree: one patched file, one additive dir set."""
    ours = tmp_path / 'ours'
    up = tmp_path / 'up'
    # upstream has the file unpatched
    make_tree(up, {'host/hle_x.c': b'line1\nline2\nline3\n'})
    # our tree has it patched, plus the additive dirs and the patch that produced it
    make_tree(ours, {
        'host/hle_x.c': b'line1\nOHOS\nline2\nline3\n',
        'ohos/front_ohos.c': b'int main(void){return 0;}\n',
        'compat/keep.c': b'',
        'gfx/vk/keep.c': b'',
        'tools/test_ohos_patches.py': GUARD.read_bytes(),
    })
    # a unified diff that turns upstream into ours
    patch = (b'--- a/host/hle_x.c\n'
             b'+++ b/host/hle_x.c\n'
             b'@@ -1,3 +1,4 @@\n'
             b' line1\n'
             b'+OHOS\n'
             b' line2\n'
             b' line3\n')
    (ours / 'ohos' / 'patches' / 'host').mkdir(parents=True)
    (ours / 'ohos' / 'patches' / 'host' / 'hle_x.c.patch').write_bytes(patch)
    return ours, up


def test_a_fresh_patch_passes(monkeypatch, trees):
    ours, up = trees
    assert run_guard(monkeypatch, ours, up) == 0


def test_a_stale_patch_fails(monkeypatch, trees):
    """The working file no longer matches what the patch produces: the queue needs re-exporting."""
    ours, up = trees
    (ours / 'host' / 'hle_x.c').write_bytes(b'line1\nOHOS\nAND MORE\nline2\nline3\n')
    assert run_guard(monkeypatch, ours, up) == 1


def test_a_patch_that_does_not_apply_fails(monkeypatch, trees):
    ours, up = trees
    (ours / 'ohos' / 'patches' / 'host' / 'hle_x.c.patch').write_bytes(
        b'--- a/host/hle_x.c\n+++ b/host/hle_x.c\n@@ -99,1 +99,1 @@\n-nope\n+never\n')
    assert run_guard(monkeypatch, ours, up) == 1


def test_a_patch_whose_target_is_not_upstream_fails(monkeypatch, trees):
    ours, up = trees
    (ours / 'ohos' / 'patches' / 'host' / 'gone.c.patch').write_bytes(
        b'--- a/host/gone.c\n+++ b/host/gone.c\n@@ -1 +1 @@\n-a\n+b\n')
    assert run_guard(monkeypatch, ours, up) == 1


def test_a_missing_additive_dir_fails(monkeypatch, trees):
    ours, up = trees
    shutil.rmtree(ours / 'compat')
    assert run_guard(monkeypatch, ours, up) == 1


def test_each_additive_dir_is_required(monkeypatch, trees):
    for d in ('ohos', 'compat', 'gfx/vk'):
        ours, up = trees
        shutil.rmtree(ours / d)
        assert run_guard(monkeypatch, ours, up) == 1


def test_upstream_drift_is_reported_but_not_a_failure(monkeypatch, trees, capsys):
    """A shared file that differs with no patch means an unsynced main: report it, do not fail the guard."""
    ours, up = trees
    (ours / 'host' / 'other.c').write_bytes(b'ours\n')
    (up / 'host' / 'other.c').write_bytes(b'theirs\n')
    code = run_guard(monkeypatch, ours, up)
    out = capsys.readouterr().out
    assert code == 0
    assert 'drift' in out
    assert 'host/other.c' in out


def test_crlf_does_not_count_as_a_difference(monkeypatch, trees):
    """The guard normalises line endings: a Windows checkout must not look stale."""
    ours, up = trees
    (ours / 'host' / 'hle_x.c').write_bytes(b'line1\r\nOHOS\r\nline2\r\nline3\r\n')
    assert run_guard(monkeypatch, ours, up) == 0


def test_it_reports_the_counts(monkeypatch, trees, capsys):
    ours, up = trees
    run_guard(monkeypatch, ours, up)
    out = capsys.readouterr().out
    assert '1 patches checked' in out
    assert '0 failures' in out


def test_a_binary_drift_file_is_not_reported(monkeypatch, trees, capsys):
    """A file with NUL bytes is not text: comparing it as text would report noise on every run."""
    ours, up = trees
    (ours / 'host' / 'blob.bin').write_bytes(b'\x00\x01\x02')
    (up / 'host' / 'blob.bin').write_bytes(b'\x00\x01\x03')
    code = run_guard(monkeypatch, ours, up)
    out = capsys.readouterr().out
    assert code == 0
    assert 'blob.bin' not in out


def test_the_patch_dir_itself_is_not_drift(monkeypatch, trees, capsys):
    """dm/ohos/patches/ lives in our tree only; it must not be compared against upstream."""
    ours, up = trees
    code = run_guard(monkeypatch, ours, up)
    out = capsys.readouterr().out
    assert code == 0
    assert 'patches/' not in out.replace('ohos/patches', '')


def test_a_nested_patch_path_is_walked(monkeypatch, trees):
    """Patches live in subdirectories mirroring dm/: the walk has to recurse."""
    ours, up = trees
    make_tree(up, {'gfx/nvn/deep.c': b'a\n'})
    make_tree(ours, {'gfx/nvn/deep.c': b'a\nb\n'})
    (ours / 'ohos' / 'patches' / 'gfx' / 'nvn').mkdir(parents=True)
    (ours / 'ohos' / 'patches' / 'gfx' / 'nvn' / 'deep.c.patch').write_bytes(
        b'--- a/gfx/nvn/deep.c\n+++ b/gfx/nvn/deep.c\n@@ -1 +1,2 @@\n a\n+b\n')
    assert run_guard(monkeypatch, ours, up) == 0
