"""daily_sync.py: the unattended upstream sync bot.

The bot is mostly a shell driver, so the tests split in two: the pure helpers (conflict classification, the CI pin
rewrite) are pinned directly, and the pipeline is driven against a fake `git` that records calls and returns
scripted output. That is what makes the abort paths testable at all - they are the ones that matter, because a
wrong abort means a human is woken up for nothing and a missed abort means a broken main.

Every test redirects the module's paths (LOG, OUTER_WF, the nested/outer roots) into tmp_path, so nothing touches
the real repository.
"""
import io
import os
import pathlib
import re

import pytest

# ---- classify: the conflict codes ----

def test_du_and_dd_keep_the_deletion(daily_sync):
    """Outside docs/ and .github/ (upstream's copy), a deleted file keeps its deletion."""
    keep, upstream, human = daily_sync.classify(['DU dm/games/x/README.md', 'DD dm/old.txt'])
    assert keep == ['dm/games/x/README.md', 'dm/old.txt']
    assert upstream == [] and human == []


def test_docs_and_github_conflicts_go_to_upstream(daily_sync):
    """docs/, .github/ and scripts/ are carried as the upstream's copy: a conflict inside them takes upstream's side.

    scripts/ joined them because the porter imports that script's siblings (pack_dump, pack_nup) at run time:
    dropping them broke every `porter inspect` and `porter port` with ModuleNotFoundError."""
    keep, upstream, human = daily_sync.classify(['DU docs/a.md', 'DD .github/workflows/tests.yml', 'UU docs/b.md',
                                                 'DU scripts/pack_dump.py', 'UU scripts/build_ipa_from_nsp.py'])
    assert keep == []
    assert [p for _, p in upstream] == ['docs/a.md', '.github/workflows/tests.yml', 'docs/b.md',
                                        'scripts/pack_dump.py', 'scripts/build_ipa_from_nsp.py']
    assert human == []


def test_a_content_conflict_needs_a_human(daily_sync):
    keep, upstream, human = daily_sync.classify(['UU dm/host/hle.c'])
    assert keep == [] and upstream == []
    assert human == [('UU', 'dm/host/hle.c')]


def test_a_mixed_status_splits_by_code(daily_sync):
    keep, upstream, human = daily_sync.classify([
        'DU docs/x.md',
        'UU dm/host/y.c',
        'AA dm/new.c',
        'DD dm/gone/z.md',
    ])
    assert keep == ['dm/gone/z.md']
    assert [p for _, p in upstream] == ['docs/x.md']
    assert human == [('UU', 'dm/host/y.c'), ('AA', 'dm/new.c')]


def test_short_lines_are_skipped(daily_sync):
    keep, upstream, human = daily_sync.classify(['', 'U', 'DU'])
    assert keep == [] and upstream == [] and human == []


def test_the_path_is_stripped_of_the_code_prefix(daily_sync):
    keep, _ups, _h = daily_sync.classify(['DU   dm/spaced.txt'])
    assert keep == ['dm/spaced.txt']


# ---- extract_pin ----

PIN = 'c365e59f61e6df41c916cdf5664afb037980fc46'


def test_extract_pin_finds_the_default(daily_sync):
    text = "  UPSTREAM_REF: ${{ vars.UPSTREAM_REF || '%s' }}\n" % PIN
    assert daily_sync.extract_pin(text) == PIN


def test_extract_pin_returns_none_without_the_expression(daily_sync):
    assert daily_sync.extract_pin('# nothing here\n') is None


def test_extract_pin_ignores_a_short_sha(daily_sync):
    """The pin is a full 40-character SHA; a short one is the comment, not the default."""
    assert daily_sync.extract_pin("UPSTREAM_REF || 'c365e59f'") is None


# ---- move_pin ----

def test_move_pin_replaces_the_full_sha(daily_sync):
    new = '8d89c3cdaabbccddeeff00112233445566778899'
    text = "UPSTREAM_REF: ${{ vars.UPSTREAM_REF || '%s' }}\n" % PIN
    moved, hit = daily_sync.move_pin(text, PIN, new)
    assert hit is True
    assert new in moved
    assert PIN not in moved


def test_move_pin_updates_the_short_comment(daily_sync):
    new = '8d89c3cdaabbccddeeff00112233445566778899'
    text = "# default, %s (short c365e59f), is the last point\n" % PIN
    moved, hit = daily_sync.move_pin(text, PIN, new)
    assert hit is True
    assert '(short 8d89c3cd)' in moved


def test_move_pin_reports_a_miss(daily_sync):
    moved, hit = daily_sync.move_pin('no pin here', PIN, 'a' * 40)
    assert hit is False
    assert moved == 'no pin here'


# ---- self_test: the module's own check, run as the CLI would ----

def test_the_modules_self_test_passes(daily_sync):
    assert daily_sync.self_test() == 0


# ---- log: appends a stamped line and echoes it ----

def test_log_appends_a_stamped_line(daily_sync, tmp_path, monkeypatch, capsys):
    log_path = tmp_path / 'out' / 'daily_sync.log'
    log_path.parent.mkdir()
    monkeypatch.setattr(daily_sync, 'LOG', str(log_path))
    daily_sync.log('hello')
    text = log_path.read_text(encoding='utf-8')
    assert 'hello' in text
    # the stamp is ISO-ish: YYYY-MM-DD HH:MM:SS
    assert re.match(r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}  hello$', text.splitlines()[0])
    assert 'hello' in capsys.readouterr().out


def test_log_appends_rather_than_truncating(daily_sync, tmp_path, monkeypatch):
    log_path = tmp_path / 'out' / 'daily_sync.log'
    log_path.parent.mkdir()
    monkeypatch.setattr(daily_sync, 'LOG', str(log_path))
    daily_sync.log('first')
    daily_sync.log('second')
    lines = log_path.read_text(encoding='utf-8').splitlines()
    assert len(lines) == 2


# ---- sh: the subprocess wrapper ----

def test_sh_returns_the_code_and_merges_stdout_stderr(daily_sync):
    rc, out = daily_sync.sh(['cmd', '/c', 'echo hi'], cwd='.')
    assert rc == 0
    assert 'hi' in out


def test_sh_reports_a_nonzero_code(daily_sync):
    rc, out = daily_sync.sh(['cmd', '/c', 'exit 3'], cwd='.')
    assert rc == 3


def test_sh_captures_stderr(daily_sync):
    rc, out = daily_sync.sh(['cmd', '/c', 'echo oops 1>&2'], cwd='.')
    assert 'oops' in out


# ---- the CLI: --self-test is the cheap path ----

def test_self_test_flag_returns_zero(daily_sync, monkeypatch):
    monkeypatch.setattr('sys.argv', ['daily_sync.py', '--self-test'])
    assert daily_sync.main() == 0


def test_the_default_game_is_the_profile_under_test(daily_sync, monkeypatch):
    """The build gate compiles one profile; the default must stay the one the port was brought up on."""
    seen = {}

    def fake_parse(self, args=None):
        raise SystemExit(0)

    monkeypatch.setattr('sys.argv', ['daily_sync.py', '--self-test'])
    daily_sync.main()
    # the default lives in the argparse setup; assert it through the help text
    import argparse
    p = argparse.ArgumentParser()
    # the module's own default is checked by reading the source's default string
    src = pathlib.Path(daily_sync.__file__).read_text(encoding='utf-8')
    assert "default='01007EF00011E000'" in src


# ---- the pipeline, against a fake git ----

class FakeGit:
    """Records git calls and answers from a script keyed on the git subcommand.

    sh() takes the argv list as its first positional argument, so the subcommand is the first entry that is not a
    -c option or its value.
    """

    def __init__(self):
        self.calls = []
        self.answers = {}

    def __call__(self, *args, **kw):
        argv = list(args[0]) if args and isinstance(args[0], (list, tuple)) else list(args)
        self.calls.append(argv)
        # argv[0] is 'git'; the subcommand is the first entry after it that is not a -c option or its value
        key = ''
        i = 1
        while i < len(argv):
            if argv[i] == '-c':
                i += 2
                continue
            if argv[i].startswith('-'):
                i += 1
                continue
            key = argv[i]
            break
        return self.answers.get(key, (0, ''))

    def called(self, name):
        return [c for c in self.calls if name in c]


@pytest.fixture
def bot(daily_sync, tmp_path, monkeypatch):
    """Point every path the bot uses into tmp_path and install a fake git."""
    nested = tmp_path / 'nested'
    outer = tmp_path / 'outer'
    (nested / 'dm' / 'tools').mkdir(parents=True)
    (nested / 'dm' / 'ohos' / 'out').mkdir(parents=True)
    (outer / '.github' / 'workflows').mkdir(parents=True)
    monkeypatch.setattr(daily_sync, 'NESTED', str(nested))
    monkeypatch.setattr(daily_sync, 'OUTER', str(outer))
    monkeypatch.setattr(daily_sync, 'OUTER_WF', str(outer / '.github' / 'workflows' / 'ci-ohos.yml'))
    monkeypatch.setattr(daily_sync, 'LOG', str(nested / 'dm' / 'ohos' / 'out' / 'daily_sync.log'))
    fake = FakeGit()
    monkeypatch.setattr(daily_sync, 'sh', fake)
    return daily_sync, fake, nested, outer


def test_a_dirty_nested_tree_aborts(bot, monkeypatch):
    ds, fake, nested, _outer = bot
    fake.answers['status'] = (0, ' M dm/host/hle.c\n')
    monkeypatch.setattr('sys.argv', ['daily_sync.py'])
    assert ds.main() == 1
    assert 'ABORT' in (nested / 'dm' / 'ohos' / 'out' / 'daily_sync.log').read_text(encoding='utf-8')


def test_a_failed_status_aborts(bot, monkeypatch):
    ds, fake, _nested, _outer = bot
    fake.answers['status'] = (128, 'not a repo')
    monkeypatch.setattr('sys.argv', ['daily_sync.py'])
    assert ds.main() == 1


def test_outer_changes_outside_the_projection_abort(bot, monkeypatch):
    """The first status is the nested tree (clean); the second is outer, with a file the bot does not own."""
    ds, _fake, _nested, _outer = bot
    seq = [(0, ''), (0, ' M README.md\n')]
    it = iter(seq)
    ds.sh = lambda *c, **k: next(it, (0, ''))
    monkeypatch.setattr('sys.argv', ['daily_sync.py'])
    assert ds.main() == 1


def test_outer_changes_inside_the_projection_do_not_abort(bot, monkeypatch):
    """The mirror lag is the bot's own work: it commits it at the end, so it must not look like a dirty tree."""
    ds, _fake, _nested, _outer = bot
    seq = [(0, ''), (0, ' M nx2ohos/nxnative/dm/host/hle.c\n'), (0, ''), (0, '2'), (0, 'a' * 40)]
    it = iter(seq)
    ds.sh = lambda *c, **k: next(it, (0, ''))
    monkeypatch.setattr('sys.argv', ['daily_sync.py', '--dry-run'])
    assert ds.main() == 0


def test_a_dry_run_reports_and_stops(bot, monkeypatch):
    ds, _fake, nested, _outer = bot
    seq = [(0, ''), (0, ''), (0, ''), (0, '3'), (0, 'a' * 40)]
    it = iter(seq)
    ds.sh = lambda *c, **k: next(it, (0, ''))
    monkeypatch.setattr('sys.argv', ['daily_sync.py', '--dry-run'])
    assert ds.main() == 0
    text = (nested / 'dm' / 'ohos' / 'out' / 'daily_sync.log').read_text(encoding='utf-8')
    assert 'dry-run: 3 new upstream commit(s)' in text


def test_nothing_new_is_up_to_date(bot, monkeypatch):
    ds, _fake, nested, _outer = bot
    seq = [(0, ''), (0, ''), (0, ''), (0, '0'), (0, 'a' * 40)]
    it = iter(seq)
    ds.sh = lambda *c, **k: next(it, (0, ''))
    monkeypatch.setattr('sys.argv', ['daily_sync.py'])
    assert ds.main() == 0
    text = (nested / 'dm' / 'ohos' / 'out' / 'daily_sync.log').read_text(encoding='utf-8')
    assert 'up to date' in text


def test_a_fetch_failure_aborts_after_retries(bot, monkeypatch):
    ds, _fake, nested, _outer = bot
    # nested clean, outer clean, then three failed fetches
    seq = [(0, ''), (0, ''), (1, 'net'), (1, 'net'), (1, 'net'), (1, 'net')]
    it = iter(seq)
    ds.sh = lambda *c, **k: next(it, (0, ''))
    monkeypatch.setattr(ds.time, 'sleep', lambda s: None)
    monkeypatch.setattr('sys.argv', ['daily_sync.py'])
    assert ds.main() == 1
    text = (nested / 'dm' / 'ohos' / 'out' / 'daily_sync.log').read_text(encoding='utf-8')
    assert 'fetch failed 3x' in text
