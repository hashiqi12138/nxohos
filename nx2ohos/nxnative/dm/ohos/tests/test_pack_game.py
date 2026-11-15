"""pack_game.py: the .nxpk header, the manifest stamp, and the pack/check round trip.

The header is the contract between this tool and the app (PackImport.ets reads it before unpacking 14 GB), so the
tests pin its exact byte layout rather than just calling the functions.
"""
import hashlib
import io
import json
import os
import struct
import tarfile

import pytest

TITLE = '01007EF00011E000'
STAMP = 'dccb848e7ac5649acd9819fabe713003d6fbf952'


def make_pack(root):
    """A minimal porter pack: the marker file plus a couple of entries, one nested."""
    (root / 'RomFS' / 'Model').mkdir(parents=True)
    (root / 'nx2a-data.json').write_text(json.dumps({'title_id': TITLE, 'name': 'Test Game'}), encoding='utf-8')
    (root / 'RomFS' / 'Model' / 'a.sbfres').write_bytes(b'\x00' * 32)
    (root / 'mods').mkdir()
    (root / 'mods' / 'main.patched.bin').write_bytes(b'\x7fELF' + b'\x00' * 12)
    (root / 'env.txt').write_text('DM_FOO=1\n', encoding='utf-8')
    return root


# ---- build_header / read_header: the byte layout the app depends on ----

def test_header_is_exactly_80_bytes(pack_game):
    h = pack_game.build_header(TITLE, STAMP, 4096)
    assert len(h) == pack_game.HEADER_SIZE == 80


def test_header_magic_and_version(pack_game):
    h = pack_game.build_header(TITLE, STAMP, 0)
    assert h[:4] == b'NXPK'
    assert h[4] == 1
    # three reserved zero bytes
    assert h[5:8] == b'\x00\x00\x00'


def test_header_title_is_nul_padded_to_16(pack_game):
    h = pack_game.build_header(TITLE, STAMP, 0)
    assert h[8:24] == TITLE.encode('ascii')
    assert len(h[8:24]) == 16


def test_header_stamp_is_nul_padded_to_48(pack_game):
    h = pack_game.build_header(TITLE, STAMP, 0)
    field = h[24:72]
    assert len(field) == 48
    assert field[:len(STAMP)] == STAMP.encode('ascii')
    assert field[len(STAMP):] == b'\x00' * (48 - len(STAMP))


def test_header_tar_size_is_little_endian_u64(pack_game):
    h = pack_game.build_header(TITLE, STAMP, 0x0102030405060708)
    assert h[72:80] == bytes([8, 7, 6, 5, 4, 3, 2, 1])
    assert struct.unpack('<Q', h[72:80])[0] == 0x0102030405060708


def test_header_round_trips_through_read(pack_game):
    raw = pack_game.build_header(TITLE, STAMP, 123456)
    got = pack_game.read_header(io.BytesIO(raw))
    assert got == {'version': 1, 'title': TITLE, 'stamp': STAMP, 'tar_size': 123456}


def test_short_title_is_padded_and_reads_back(pack_game):
    raw = pack_game.build_header('ABC', 'deadbeef', 0)
    got = pack_game.read_header(io.BytesIO(raw))
    assert got['title'] == 'ABC'
    assert got['stamp'] == 'deadbeef'


def test_title_longer_than_16_is_refused(pack_game):
    with pytest.raises(SystemExit, match='title ID longer than 16'):
        pack_game.build_header('X' * 17, STAMP, 0)


def test_stamp_longer_than_48_is_refused(pack_game):
    with pytest.raises(SystemExit, match='stamp longer than 48'):
        pack_game.build_header(TITLE, 'a' * 49, 0)


def test_read_rejects_a_short_file(pack_game):
    with pytest.raises(SystemExit, match='shorter than the header'):
        pack_game.read_header(io.BytesIO(b'NXPK' + b'\x00' * 40))


def test_read_rejects_a_wrong_magic(pack_game):
    bad = b'ZIPK' + b'\x00' * 76
    with pytest.raises(SystemExit, match='not a game pack'):
        pack_game.read_header(io.BytesIO(bad))


# ---- manifest_stamp ----

def test_manifest_stamp_reads_the_header_line(pack_game, tmp_path):
    man = tmp_path / 'dm.files.tsv'
    man.write_text('# DM_DATA %s\nRomFS/a\t10\t5\n' % STAMP, encoding='utf-8')
    assert pack_game.manifest_stamp(str(man)) == STAMP


def test_manifest_stamp_is_empty_without_the_header(pack_game, tmp_path):
    man = tmp_path / 'dm.files.tsv'
    man.write_text('RomFS/a\t10\t5\n', encoding='utf-8')
    assert pack_game.manifest_stamp(str(man)) == ''


def test_manifest_stamp_tolerates_extra_spaces(pack_game, tmp_path):
    man = tmp_path / 'dm.files.tsv'
    man.write_text('# DM_DATA   %s  \n' % STAMP, encoding='utf-8')
    assert pack_game.manifest_stamp(str(man)) == STAMP


def test_manifest_stamp_handles_undecodable_bytes(pack_game, tmp_path):
    man = tmp_path / 'dm.files.tsv'
    man.write_bytes(b'# DM_DATA abc\n\xff\xfe\n')
    assert pack_game.manifest_stamp(str(man)) == 'abc'


# ---- ensure_manifest ----

def test_ensure_manifest_returns_an_existing_file(pack_game, tmp_path):
    man = tmp_path / 'dm.files.tsv'
    man.write_text('# DM_DATA x\n', encoding='utf-8')
    assert pack_game.ensure_manifest(str(tmp_path)) == str(man)


def test_ensure_manifest_returns_none_when_the_generator_is_absent(pack_game, tmp_path, monkeypatch):
    real = os.path.isfile

    def fake(p):
        return False if p.endswith('gen_pack_manifest.py') else real(p)

    monkeypatch.setattr(pack_game.os.path, 'isfile', fake)
    assert pack_game.ensure_manifest(str(tmp_path)) is None


def test_ensure_manifest_returns_none_when_the_generator_fails(pack_game, tmp_path, monkeypatch):
    class R:
        returncode = 1
    monkeypatch.setattr(pack_game.subprocess, 'run', lambda *a, **k: R())
    assert pack_game.ensure_manifest(str(tmp_path)) is None


# ---- build: the whole pack ----

def test_build_writes_a_container_the_app_can_read(pack_game, tmp_path):
    src = make_pack(tmp_path / 'pack')
    out = tmp_path / 'game.nxpk'
    pack_game.build(str(src), TITLE, str(out))

    assert out.is_file()
    with open(out, 'rb') as f:
        h = pack_game.read_header(f)
    assert h['title'] == TITLE
    assert h['version'] == 1
    assert h['tar_size'] == out.stat().st_size - pack_game.HEADER_SIZE


def test_build_leaves_the_manifest_out_of_the_body(pack_game, tmp_path):
    src = make_pack(tmp_path / 'pack')
    (src / 'dm.files.tsv').write_text('# DM_DATA %s\n' % STAMP, encoding='utf-8')
    out = tmp_path / 'game.nxpk'
    pack_game.build(str(src), TITLE, str(out))

    with open(out, 'rb') as f:
        f.seek(pack_game.HEADER_SIZE)
        with tarfile.open(fileobj=f, mode='r|') as tf:
            names = [m.name for m in tf]
    # the manifest rides in the header; the app must not unpack it
    assert 'dm.files.tsv' not in names
    assert 'RomFS' in names
    assert 'nx2a-data.json' in names


def test_build_carries_the_manifest_stamp_into_the_header(pack_game, tmp_path):
    src = make_pack(tmp_path / 'pack')
    (src / 'dm.files.tsv').write_text('# DM_DATA %s\n' % STAMP, encoding='utf-8')
    out = tmp_path / 'game.nxpk'
    pack_game.build(str(src), TITLE, str(out))
    with open(out, 'rb') as f:
        assert pack_game.read_header(f)['stamp'] == STAMP


def test_build_stamps_the_tree_when_there_is_no_manifest(pack_game, tmp_path, monkeypatch):
    """No dm.files.tsv and no generator: the stamp falls back to hashing the tree, so the app still sees change."""
    src = make_pack(tmp_path / 'pack')
    monkeypatch.setattr(pack_game, 'ensure_manifest', lambda p: None)
    out = tmp_path / 'game.nxpk'
    pack_game.build(str(src), TITLE, str(out))

    with open(out, 'rb') as f:
        stamp = pack_game.read_header(f)['stamp']
    assert len(stamp) == 40
    int(stamp, 16)   # a sha1 hex digest


def test_build_refuses_a_missing_pack_dir(pack_game, tmp_path):
    with pytest.raises(SystemExit, match='no such pack dir'):
        pack_game.build(str(tmp_path / 'nope'), TITLE, str(tmp_path / 'o.nxpk'))


def test_build_refuses_a_dir_without_the_marker(pack_game, tmp_path):
    d = tmp_path / 'empty'
    d.mkdir()
    with pytest.raises(SystemExit, match='has no nx2a-data.json'):
        pack_game.build(str(d), TITLE, str(tmp_path / 'o.nxpk'))


def test_build_removes_its_temporary_tar(pack_game, tmp_path):
    src = make_pack(tmp_path / 'pack')
    out = tmp_path / 'game.nxpk'
    pack_game.build(str(src), TITLE, str(out))
    assert not os.path.exists(str(out) + '.tar.tmp')


# ---- check ----

def test_check_accepts_a_pack_it_just_built(pack_game, tmp_path, capsys):
    src = make_pack(tmp_path / 'pack')
    out = tmp_path / 'game.nxpk'
    pack_game.build(str(src), TITLE, str(out))
    capsys.readouterr()

    assert pack_game.check(str(out)) == 0
    text = capsys.readouterr().out
    assert 'consistent' in text
    assert 'version 1' in text
    assert TITLE in text


def test_check_counts_the_tar_members(pack_game, tmp_path, capsys):
    src = make_pack(tmp_path / 'pack')
    out = tmp_path / 'game.nxpk'
    pack_game.build(str(src), TITLE, str(out))
    capsys.readouterr()
    pack_game.check(str(out))
    # every member, not just the top-level entries: RomFS, RomFS/Model, RomFS/Model/a.sbfres,
    # mods, mods/main.patched.bin, env.txt, nx2a-data.json
    assert 'members: 7' in capsys.readouterr().out


def test_check_reports_a_truncated_file(pack_game, tmp_path, capsys):
    src = make_pack(tmp_path / 'pack')
    out = tmp_path / 'game.nxpk'
    pack_game.build(str(src), TITLE, str(out))
    data = out.read_bytes()
    out.write_bytes(data[:-16])
    capsys.readouterr()

    assert pack_game.check(str(out)) == 1
    assert 'MISMATCH' in capsys.readouterr().out


def test_check_rejects_a_non_pack(pack_game, tmp_path):
    # 80 bytes of the wrong magic: long enough to pass the length check, so the magic check is what fires.
    p = tmp_path / 'x.bin'
    p.write_bytes(b'NOTPACK!' + b'\x00' * 72)
    with pytest.raises(SystemExit, match='not a game pack'):
        pack_game.check(str(p))


# ---- main ----

def test_main_builds_when_given_a_pack(pack_game, tmp_path, monkeypatch):
    src = make_pack(tmp_path / 'pack')
    out = tmp_path / 'via-main.nxpk'
    monkeypatch.setattr('sys.argv', ['pack_game.py', '--pack', str(src), '--title', TITLE, '--out', str(out)])
    pack_game.main()
    assert out.is_file()


def test_main_defaults_the_output_beside_the_pack(pack_game, tmp_path, monkeypatch):
    src = make_pack(tmp_path / 'pack')
    monkeypatch.setattr('sys.argv', ['pack_game.py', '--pack', str(src), '--title', TITLE])
    pack_game.main()
    assert (tmp_path / (TITLE + '.nxpk')).is_file()


def test_main_checks_when_asked(pack_game, tmp_path, monkeypatch):
    src = make_pack(tmp_path / 'pack')
    out = tmp_path / 'game.nxpk'
    pack_game.build(str(src), TITLE, str(out))
    monkeypatch.setattr('sys.argv', ['pack_game.py', '--pack', str(src), '--title', TITLE, '--check', str(out)])
    with pytest.raises(SystemExit) as e:
        pack_game.main()
    assert e.value.code == 0
