"""fix_utf8_dashes.py: repairing the mojibake a PowerShell round-trip leaves in UTF-8 sources.

The damage is specific: a UTF-8 sequence whose lead bytes survive and whose last byte became 0x3F ("?"), because
the host's console page rewrote it. Each test builds those bytes directly, so the repair is pinned to the exact
sequences it claims to handle.
"""
import pytest

TOOL = __import__('pathlib').Path(__file__).resolve().parents[2] / 'tools' / 'fix_utf8_dashes.py'

EM_DASH = '—'.encode('utf-8')        # e2 80 94
EN_DASH = '–'.encode('utf-8')        # e2 80 93
ARROW = '→'.encode('utf-8')          # e2 86 92
LE = '≤'.encode('utf-8')             # e2 89 a4
CHECK = '✅'.encode('utf-8')          # e2 9c 85


def damage(seq):
    """What the console-page round-trip leaves: the lead bytes, then '?' for the final byte."""
    return seq[:-1] + b'?'


def write(p, data):
    p.write_bytes(data)
    return p


# ---- the four sequences the tool names ----

def test_em_dash_becomes_two_hyphens(fix_dashes, tmp_path, capsys):
    f = write(tmp_path / 'a.txt', b'a ' + damage(EM_DASH) + b' b')
    assert fix_dashes.fix(str(f)) is True
    assert f.read_bytes() == b'a -- b'
    assert '1 dash sequence(s) fixed' in capsys.readouterr().out


def test_en_dash_becomes_two_hyphens(fix_dashes, tmp_path):
    f = write(tmp_path / 'a.txt', b'x' + damage(EN_DASH) + b'y')
    fix_dashes.fix(str(f))
    assert f.read_bytes() == b'x--y'


def test_right_arrow_becomes_ascii_arrow(fix_dashes, tmp_path):
    f = write(tmp_path / 'a.txt', b'go ' + damage(ARROW) + b' here')
    fix_dashes.fix(str(f))
    assert f.read_bytes() == b'go -> here'


def test_comparison_becomes_less_equal(fix_dashes, tmp_path):
    f = write(tmp_path / 'a.txt', b'a ' + damage(LE) + b' b')
    fix_dashes.fix(str(f))
    assert f.read_bytes() == b'a <= b'


def test_check_mark_becomes_ok(fix_dashes, tmp_path):
    f = write(tmp_path / 'a.txt', b'status ' + damage(CHECK))
    fix_dashes.fix(str(f))
    assert f.read_bytes() == b'status OK'


def test_an_unknown_sequence_becomes_a_question_mark(fix_dashes, tmp_path):
    """A lead pair the table does not name still has to be removed, or the file stays broken."""
    f = write(tmp_path / 'a.txt', b'a ' + b'\xe2\x99?' + b' b')
    fix_dashes.fix(str(f))
    assert f.read_bytes() == b'a ? b'


# ---- what must not be touched ----

def test_a_clean_file_is_unchanged(fix_dashes, tmp_path):
    f = write(tmp_path / 'a.txt', b'all ascii, nothing to do\n')
    fix_dashes.fix(str(f))
    assert f.read_bytes() == b'all ascii, nothing to do\n'


def test_valid_utf8_cjk_survives(fix_dashes, tmp_path):
    """A CJK comment is a real character, not damage: it must come through byte for byte."""
    original = '中文注释'.encode('utf-8')
    f = write(tmp_path / 'a.txt', original)
    assert fix_dashes.fix(str(f)) is True
    assert f.read_bytes() == original


def test_a_valid_dash_is_left_alone(fix_dashes, tmp_path):
    """The damage is a truncated sequence; an intact em dash is fine and must not be rewritten."""
    original = b'a ' + EM_DASH + b' b'
    f = write(tmp_path / 'a.txt', original)
    fix_dashes.fix(str(f))
    assert f.read_bytes() == original


def test_a_file_without_damage_is_not_rewritten(fix_dashes, tmp_path):
    """No change means no write: the mtime stays, so a rebuild is not triggered by a no-op."""
    f = write(tmp_path / 'a.txt', b'plain\n')
    before = f.stat().st_mtime_ns
    fix_dashes.fix(str(f))
    assert f.stat().st_mtime_ns == before


# ---- the byte-walk edge cases ----

def test_a_sequence_at_the_very_end_is_handled(fix_dashes, tmp_path):
    f = write(tmp_path / 'a.txt', b'tail ' + damage(EM_DASH))
    fix_dashes.fix(str(f))
    assert f.read_bytes() == b'tail --'


def test_two_adjacent_sequences_both_fix(fix_dashes, tmp_path):
    # em dash -> '--', arrow -> '->', concatenated: '--->'
    f = write(tmp_path / 'a.txt', damage(EM_DASH) + damage(ARROW))
    fix_dashes.fix(str(f))
    assert f.read_bytes() == b'--->'


def test_a_truncated_pair_at_eof_is_left_as_bytes(fix_dashes, tmp_path):
    """Fewer than three bytes left cannot be a damaged sequence, so they pass through."""
    f = write(tmp_path / 'a.txt', b'\xe2\x80')
    fix_dashes.fix(str(f))
    assert f.read_bytes() == b'\xe2\x80'


def test_a_lead_byte_without_the_question_mark_is_kept(fix_dashes, tmp_path):
    """e2 80 followed by a real byte is not this damage; the walk must not eat it."""
    f = write(tmp_path / 'a.txt', b'\xe2\x80\x94')
    fix_dashes.fix(str(f))
    assert f.read_bytes() == b'\xe2\x80\x94'


def test_e3_lead_is_recognised(fix_dashes, tmp_path):
    """The walk accepts e3 as a lead too (CJK punctuation); an unmapped pair becomes '?'."""
    f = write(tmp_path / 'a.txt', b'\xe3\x80?')
    fix_dashes.fix(str(f))
    assert f.read_bytes() == b'?'


def test_remaining_invalid_utf8_is_reported(fix_dashes, tmp_path, capsys):
    """A break the table does not cover leaves the file invalid, and the tool says so."""
    f = write(tmp_path / 'a.txt', b'\xff\xfe broken')
    ok = fix_dashes.fix(str(f))
    out = capsys.readouterr().out
    assert ok is False
    assert 'STILL INVALID UTF-8' in out
    assert 'valid UTF-8: False' in out


def test_the_report_counts_the_sequences_and_the_sizes(fix_dashes, tmp_path, capsys):
    f = write(tmp_path / 'a.txt', b'x' + damage(EM_DASH) + damage(EM_DASH) + b'y')
    fix_dashes.fix(str(f))
    out = capsys.readouterr().out
    assert '2 dash sequence(s) fixed' in out
    # each 3-byte sequence becomes a 2-byte stand-in: x + 3 + 3 + y = 8 bytes -> x + 2 + 2 + y = 6
    assert '8 -> 6 bytes' in out


# ---- the CLI: the __main__ block is what enforces the usage message and the exit code ----

def run_main(monkeypatch, argv):
    """Execute the module's __main__ block, which is the only place the CLI logic lives."""
    import runpy
    monkeypatch.setattr('sys.argv', argv)
    return runpy.run_path(str(TOOL), run_name='__main__')


def test_cli_fixes_every_file_named(monkeypatch, tmp_path):
    a = write(tmp_path / 'a.txt', damage(EM_DASH))
    b = write(tmp_path / 'b.txt', damage(ARROW))
    with pytest.raises(SystemExit) as e:
        run_main(monkeypatch, ['fix_utf8_dashes.py', str(a), str(b)])
    assert e.value.code == 0
    assert a.read_bytes() == b'--'
    assert b.read_bytes() == b'->'


def test_cli_exits_1_when_a_file_stays_invalid(monkeypatch, tmp_path):
    f = write(tmp_path / 'a.txt', b'\xff broken')
    with pytest.raises(SystemExit) as e:
        run_main(monkeypatch, ['fix_utf8_dashes.py', str(f)])
    assert e.value.code == 1


def test_cli_without_arguments_prints_usage(monkeypatch):
    with pytest.raises(SystemExit) as e:
        run_main(monkeypatch, ['fix_utf8_dashes.py'])
    assert 'usage: fix_utf8_dashes.py' in str(e.value.code)
