"""gen_pack_manifest.py: dm.files.tsv, the stamp over (rel, size, mtime_ns).

The stamp is what makes a repeat import cost nothing: the app compares it against its sandbox's .stamp and skips
the 14 GB pull when they match. So the tests pin both what goes into the hash and what the file looks like.
"""
import hashlib

import pytest


def make_pack(root):
    (root / 'RomFS' / 'Model').mkdir(parents=True)
    (root / 'RomFS' / 'Model' / 'a.sbfres').write_bytes(b'A' * 10)
    (root / 'env.txt').write_text('X=1\n', encoding='utf-8')
    (root / 'mods').mkdir()
    (root / 'mods' / 'main.patched.bin').write_bytes(b'M' * 5)
    return root


def run(gen_manifest, monkeypatch, pack, out):
    monkeypatch.setattr('sys.argv', ['gen_pack_manifest.py', '--pack', str(pack), '--out', str(out)])
    gen_manifest.main()


def expected_stamp(pack, rels):
    h = hashlib.sha1()
    for rel in rels:
        st = (pack / rel).stat()
        h.update(('%s\t%d\t%d\n' % (rel, st.st_size, st.st_mtime_ns)).encode())
    return h.hexdigest()


# ---- the file's shape ----

def test_header_is_the_dm_data_line(gen_manifest, tmp_path, monkeypatch, capsys):
    pack = make_pack(tmp_path / 'p')
    out = tmp_path / 'dm.files.tsv'
    run(gen_manifest, monkeypatch, pack, out)
    first = out.read_text(encoding='utf-8').splitlines()[0]
    assert first.startswith('# DM_DATA ')
    assert len(first[len('# DM_DATA '):]) == 40   # a sha1 hex digest


def test_one_line_per_file_with_size_and_module(gen_manifest, tmp_path, monkeypatch):
    pack = make_pack(tmp_path / 'p')
    out = tmp_path / 'dm.files.tsv'
    run(gen_manifest, monkeypatch, pack, out)
    lines = out.read_text(encoding='utf-8').splitlines()[1:]
    # sizes come from the files themselves, not from a constant: write_text may translate newlines on Windows.
    expect = [
        'RomFS/Model/a.sbfres\t%d\tentry' % (pack / 'RomFS/Model/a.sbfres').stat().st_size,
        'env.txt\t%d\tentry' % (pack / 'env.txt').stat().st_size,
        'mods/main.patched.bin\t%d\tentry' % (pack / 'mods/main.patched.bin').stat().st_size,
    ]
    assert lines == expect


def test_paths_use_forward_slashes(gen_manifest, tmp_path, monkeypatch):
    """A backslash would make the app look for a file name that contains one."""
    pack = make_pack(tmp_path / 'p')
    out = tmp_path / 'dm.files.tsv'
    run(gen_manifest, monkeypatch, pack, out)
    text = out.read_text(encoding='utf-8')
    assert '\\' not in text


def test_files_are_sorted(gen_manifest, tmp_path, monkeypatch):
    pack = make_pack(tmp_path / 'p')
    out = tmp_path / 'dm.files.tsv'
    run(gen_manifest, monkeypatch, pack, out)
    rels = [l.split('\t')[0] for l in out.read_text(encoding='utf-8').splitlines()[1:]]
    assert rels == sorted(rels)


def test_the_output_ends_with_a_newline(gen_manifest, tmp_path, monkeypatch):
    pack = make_pack(tmp_path / 'p')
    out = tmp_path / 'dm.files.tsv'
    run(gen_manifest, monkeypatch, pack, out)
    assert out.read_bytes().endswith(b'\n')


def test_the_output_uses_lf_not_crlf(gen_manifest, tmp_path, monkeypatch):
    """A CRLF manifest would hash differently on Windows and make every import look stale."""
    pack = make_pack(tmp_path / 'p')
    out = tmp_path / 'dm.files.tsv'
    run(gen_manifest, monkeypatch, pack, out)
    assert b'\r\n' not in out.read_bytes()


# ---- the stamp ----

def test_stamp_matches_a_manual_hash(gen_manifest, tmp_path, monkeypatch):
    pack = make_pack(tmp_path / 'p')
    out = tmp_path / 'dm.files.tsv'
    run(gen_manifest, monkeypatch, pack, out)
    got = out.read_text(encoding='utf-8').splitlines()[0][len('# DM_DATA '):]
    assert got == expected_stamp(pack, ['RomFS/Model/a.sbfres', 'env.txt', 'mods/main.patched.bin'])


def test_stamp_changes_when_a_file_changes(gen_manifest, tmp_path, monkeypatch):
    pack = make_pack(tmp_path / 'p')
    out = tmp_path / 'dm.files.tsv'
    run(gen_manifest, monkeypatch, pack, out)
    first = out.read_text(encoding='utf-8').splitlines()[0]

    (pack / 'env.txt').write_text('X=22\n', encoding='utf-8')
    run(gen_manifest, monkeypatch, pack, out)
    second = out.read_text(encoding='utf-8').splitlines()[0]
    assert first != second


def test_stamp_is_stable_across_runs(gen_manifest, tmp_path, monkeypatch):
    """Same tree, same stamp: a rebuild that changed nothing must not trigger a re-import."""
    pack = make_pack(tmp_path / 'p')
    a = tmp_path / 'a.tsv'
    b = tmp_path / 'b.tsv'
    run(gen_manifest, monkeypatch, pack, a)
    run(gen_manifest, monkeypatch, pack, b)
    assert a.read_text(encoding='utf-8').splitlines()[0] == b.read_text(encoding='utf-8').splitlines()[0]


def test_an_existing_manifest_is_not_hashed_into_itself(gen_manifest, tmp_path, monkeypatch):
    """dm.files.tsv lives in the pack: including it would make the stamp depend on the previous stamp."""
    pack = make_pack(tmp_path / 'p')
    out = pack / 'dm.files.tsv'
    run(gen_manifest, monkeypatch, pack, out)
    first = out.read_text(encoding='utf-8').splitlines()[0]
    # a second run rewrites the same path; the file is skipped from its own walk
    run(gen_manifest, monkeypatch, pack, out)
    assert out.read_text(encoding='utf-8').splitlines()[0] == first
    assert 'dm.files.tsv' not in out.read_text(encoding='utf-8')


def test_the_stamp_covers_the_mtime_not_only_the_size(gen_manifest, tmp_path, monkeypatch):
    """Two writes of the same length must still read as different data."""
    pack = make_pack(tmp_path / 'p')
    out = tmp_path / 'dm.files.tsv'
    run(gen_manifest, monkeypatch, pack, out)
    first = out.read_text(encoding='utf-8').splitlines()[0]

    f = pack / 'env.txt'
    f.write_text('X=1\n', encoding='utf-8')     # same bytes, new mtime
    import os
    st = f.stat()
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    run(gen_manifest, monkeypatch, pack, out)
    assert out.read_text(encoding='utf-8').splitlines()[0] != first


# ---- the CLI ----

def test_a_missing_pack_dir_exits(gen_manifest, tmp_path, monkeypatch):
    monkeypatch.setattr('sys.argv', ['gen_pack_manifest.py', '--pack', str(tmp_path / 'nope'),
                                     '--out', str(tmp_path / 'o.tsv')])
    with pytest.raises(SystemExit, match='no such pack'):
        gen_manifest.main()


def test_an_empty_pack_produces_a_header_and_nothing_else(gen_manifest, tmp_path, monkeypatch, capsys):
    pack = tmp_path / 'empty'
    pack.mkdir()
    out = tmp_path / 'dm.files.tsv'
    run(gen_manifest, monkeypatch, pack, out)
    lines = out.read_text(encoding='utf-8').splitlines()
    assert len(lines) == 1
    assert lines[0].startswith('# DM_DATA ')
    # the stamp of an empty tree is sha1 of nothing
    assert lines[0] == '# DM_DATA ' + hashlib.sha1(b'').hexdigest()


def test_it_reports_the_count_and_a_short_stamp(gen_manifest, tmp_path, monkeypatch, capsys):
    pack = make_pack(tmp_path / 'p')
    out = tmp_path / 'dm.files.tsv'
    run(gen_manifest, monkeypatch, pack, out)
    text = capsys.readouterr().out
    assert '3 files' in text
    assert 'stamp ' in text
