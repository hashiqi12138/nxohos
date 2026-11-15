"""daily_sync.py, the full pipeline: merge -> gates -> pin -> mirror -> push.

The bot is a shell driver, so each test scripts the sequence of (returncode, output) pairs git and the sub-tools
return, then asserts on the log line the bot wrote. The point is the abort paths: a wrong abort wakes a human for
nothing, and a missed one leaves a broken main.

Every path the bot uses is redirected into tmp_path, and `sh` is replaced, so nothing touches the real repository.
"""
import pathlib

import pytest


class Script:
    """Answers sh() calls in order, keyed by the subcommand when a test wants that instead."""

    def __init__(self):
        self.calls = []
        self.by_key = {}
        self.queue = []

    def __call__(self, argv, cwd=None, timeout=None):
        argv = list(argv)
        self.calls.append(argv)
        if self.queue:
            return self.queue.pop(0)
        key = ''
        i = 1 if argv and argv[0] == 'git' else 0
        while i < len(argv):
            if argv[i] == '-c':
                i += 2
                continue
            if argv[i].startswith('-'):
                i += 1
                continue
            key = argv[i]
            break
        return self.by_key.get(key, (0, ''))

    def keys(self):
        out = []
        for argv in self.calls:
            i = 1 if argv and argv[0] == 'git' else 0
            while i < len(argv):
                if argv[i] == '-c':
                    i += 2
                    continue
                if argv[i].startswith('-'):
                    i += 1
                    continue
                out.append(argv[i])
                break
        return out


@pytest.fixture
def env(daily_sync, tmp_path, monkeypatch):
    """The bot with every path in tmp_path, a scripted sh, and a workflow file that carries a pin."""
    nested = tmp_path / 'nested'
    outer = tmp_path / 'outer'
    (nested / 'dm' / 'ohos' / 'out').mkdir(parents=True)
    wf = outer / '.github' / 'workflows' / 'ci-ohos.yml'
    wf.parent.mkdir(parents=True)
    wf.write_text("  UPSTREAM_REF: ${{ vars.UPSTREAM_REF || '%s' }}\n" % ('c' * 40), encoding='utf-8')

    monkeypatch.setattr(daily_sync, 'NESTED', str(nested))
    monkeypatch.setattr(daily_sync, 'OUTER', str(outer))
    monkeypatch.setattr(daily_sync, 'OUTER_WF', str(wf))
    monkeypatch.setattr(daily_sync, 'LOG', str(nested / 'dm' / 'ohos' / 'out' / 'daily_sync.log'))
    monkeypatch.setattr(daily_sync.time, 'sleep', lambda s: None)
    script = Script()
    monkeypatch.setattr(daily_sync, 'sh', script)
    return daily_sync, script, nested, outer, wf


def run(env, monkeypatch, argv):
    ds, script, nested, outer, wf = env
    monkeypatch.setattr('sys.argv', ['daily_sync.py'] + argv)
    rc = ds.main()
    log = (nested / 'dm' / 'ohos' / 'out' / 'daily_sync.log')
    return rc, (log.read_text(encoding='utf-8') if log.is_file() else '')


UP = 'a' * 40


def preflight_clean(script, n_new='1'):
    """The four calls main() makes before the merge: two statuses, a fetch, the count, the sha."""
    script.queue = [(0, ''), (0, ''), (0, ''), (0, n_new), (0, UP)]


# ---- the merge stage ----

def test_a_content_conflict_aborts_and_cleans_up(env, monkeypatch):
    ds, script, _n, _o, _w = env
    preflight_clean(script)
    script.queue += [
        (1, ''),                      # rev-parse --verify branch: does not exist
        (0, ''),                      # checkout -b
        (1, 'conflict'),              # sync_upstream fails
        (0, 'UU dm/host/hle.c\n'),    # status: a real content conflict
        (0, ''),                      # merge --abort
        (0, ''),                      # checkout main
        (0, ''),                      # branch -D
    ]
    rc, log = run(env, monkeypatch, [])
    assert rc == 1
    assert 'content conflict(s) need a human' in log


def test_a_docs_conflict_keeps_the_deletion(env, monkeypatch):
    """The port snapshot carries no docs/: an upstream docs file must not stop the bot."""
    ds, script, _n, _o, _w = env
    preflight_clean(script)
    script.queue += [
        (1, ''), (0, ''),                                  # branch create
        (1, 'conflict'),                                    # sync_upstream fails
        (0, 'UU docs/new.md\n'),                            # a docs conflict
        (0, ''),                                            # git rm
        (0, ''),                                            # diff --name-only --diff-filter=U: clean
        (0, ''),                                            # commit
        (0, ''), (0, ''), (0, ''),                          # the three gates
        (0, ''),                                            # git add patches
        (0, ' M dm/ohos/patches/x.patch\n'),                # status: queue changed
        (0, ''),                                            # commit the queue
        (0, ''),                                            # checkout main
        (0, ''),                                            # merge --ff-only
        (0, ''),                                            # branch -d
        (0, 'abc1234'),                                     # rev-parse --short HEAD
        (0, ''),                                            # git add outer
        (0, ' M nx2ohos\n'),                                # status outer: something to commit
        (0, ''),                                            # commit outer
        (0, ''),                                            # push
        (0, 'def5678'),                                     # rev-parse outer
    ]
    rc, log = run(env, monkeypatch, [])
    assert rc == 0
    assert 'keep-deletion conflict' in log


def test_unmerged_files_left_behind_abort(env, monkeypatch):
    ds, script, _n, _o, _w = env
    preflight_clean(script)
    script.queue += [
        (1, ''), (0, ''),
        (1, 'conflict'),
        (0, 'DU docs/a.md\n'),
        (0, ''),                       # git rm
        (0, 'docs/a.md\n'),            # diff --diff-filter=U still lists it
        (0, ''), (0, ''), (0, ''),     # cleanup
    ]
    rc, log = run(env, monkeypatch, [])
    assert rc == 1
    assert 'unmerged files remain' in log


def test_a_clean_merge_needs_no_conflict_pass(env, monkeypatch):
    ds, script, _n, _o, _w = env
    preflight_clean(script)
    script.queue += [
        (1, ''), (0, ''),              # branch create
        (0, ''),                        # sync_upstream succeeds: clean merge
        (0, ''), (0, ''), (0, ''),      # gates
        (0, ''),                        # add patches
        (0, ''),                        # status: nothing new
        (0, ''),                        # checkout main
        (0, ''),                        # ff merge
        (0, ''),                        # branch -d
        (0, 'abc1234'),                 # rev-parse nested
        (0, ''),                        # add outer
        (0, ' M nx2ohos\n'),            # status outer
        (0, ''),                        # commit outer
        (0, ''),                        # push
        (0, 'def5678'),
    ]
    rc, log = run(env, monkeypatch, [])
    assert rc == 0
    assert 'clean' in log


# ---- the three gates ----

@pytest.mark.parametrize('failing,expected', [
    (0, 'export gate red'),
    (1, 'guard gate red'),
    (2, 'build gate red'),
])
def test_a_red_gate_aborts(env, monkeypatch, failing, expected):
    ds, script, _n, _o, _w = env
    preflight_clean(script)
    gates = [(0, 'ok'), (0, 'ok'), (0, 'ok')]
    gates[failing] = (1, 'boom')
    script.queue += [
        (1, ''), (0, ''),
        (0, ''),                       # sync_upstream ok
        *gates,
        (0, ''), (0, ''), (0, ''),     # cleanup
    ]
    rc, log = run(env, monkeypatch, [])
    assert rc == 1
    assert expected in log


def test_a_green_gate_is_logged_with_its_tail(env, monkeypatch):
    ds, script, _n, _o, _w = env
    preflight_clean(script)
    script.queue += [
        (1, ''), (0, ''),
        (0, ''),
        (0, 'export: 16 patches'), (0, 'guard: ok'), (0, 'build: linked'),
        (0, ''), (0, ''),
        (0, ''), (0, ''), (0, ''), (0, 'abc1234'),
        (0, ''), (0, ' M x\n'), (0, ''), (0, ''), (0, 'def5678'),
    ]
    rc, log = run(env, monkeypatch, [])
    assert rc == 0
    assert 'export gate green: export: 16 patches' in log
    assert 'guard gate green: guard: ok' in log
    assert 'build gate green: build: linked' in log


# ---- the CI pin ----

def test_the_pin_moves_to_the_merged_sha(env, monkeypatch):
    ds, script, _n, _o, wf = env
    preflight_clean(script)
    script.queue += [
        (1, ''), (0, ''), (0, ''),
        (0, ''), (0, ''), (0, ''),
        (0, ''), (0, ''),
        (0, ''), (0, ''), (0, ''), (0, 'abc1234'),
        (0, ''), (0, ' M x\n'), (0, ''), (0, ''), (0, 'def5678'),
    ]
    rc, log = run(env, monkeypatch, [])
    assert rc == 0
    assert UP in wf.read_text(encoding='utf-8')
    assert 'CI pin moved' in log


def test_a_missing_pin_warns_without_failing(env, monkeypatch):
    ds, script, _n, _o, wf = env
    wf.write_text('# no pin here\n', encoding='utf-8')
    preflight_clean(script)
    script.queue += [
        (1, ''), (0, ''), (0, ''),
        (0, ''), (0, ''), (0, ''),
        (0, ''), (0, ''),
        (0, ''), (0, ''), (0, ''), (0, 'abc1234'),
        (0, ''), (0, ' M x\n'), (0, ''), (0, ''), (0, 'def5678'),
    ]
    rc, log = run(env, monkeypatch, [])
    assert rc == 0
    assert 'no UPSTREAM_REF pin found' in log


# ---- the mirror and push ----

def test_an_unchanged_mirror_stops_without_pushing(env, monkeypatch):
    ds, script, _n, _o, _w = env
    preflight_clean(script)
    script.queue += [
        (1, ''), (0, ''), (0, ''),
        (0, ''), (0, ''), (0, ''),
        (0, ''), (0, ''),
        (0, ''), (0, ''), (0, ''), (0, 'abc1234'),
        (0, ''), (0, ''),               # add + status: nothing to commit
    ]
    rc, log = run(env, monkeypatch, [])
    assert rc == 0
    assert 'outer mirror unchanged' in log


def test_no_push_keeps_the_commit_local(env, monkeypatch):
    ds, script, _n, _o, _w = env
    preflight_clean(script)
    script.queue += [
        (1, ''), (0, ''), (0, ''),
        (0, ''), (0, ''), (0, ''),
        (0, ''), (0, ''),
        (0, ''), (0, ''), (0, ''), (0, 'abc1234'),
        (0, ''), (0, ' M x\n'), (0, ''),
        (0, 'def5678'),
    ]
    rc, log = run(env, monkeypatch, ['--no-push'])
    assert rc == 0
    assert 'no-push: outer commit kept local' in log


def test_a_push_that_keeps_failing_aborts(env, monkeypatch):
    ds, script, _n, _o, _w = env
    preflight_clean(script)
    script.queue += [
        (1, ''), (0, ''), (0, ''),
        (0, ''), (0, ''), (0, ''),
        (0, ''), (0, ''),
        (0, ''), (0, ''), (0, ''), (0, 'abc1234'),
        (0, ''), (0, ' M x\n'), (0, ''),
        (1, 'net'), (1, 'net'), (1, 'net'),
    ]
    rc, log = run(env, monkeypatch, [])
    assert rc == 1
    assert 'push failed 3x' in log


def test_a_successful_push_is_logged_with_the_counts(env, monkeypatch):
    ds, script, _n, _o, _w = env
    preflight_clean(script, n_new='7')
    script.queue += [
        (1, ''), (0, ''), (0, ''),
        (0, ''), (0, ''), (0, ''),
        (0, ''), (0, ''),
        (0, ''), (0, ''), (0, ''), (0, 'abc1234'),
        (0, ''), (0, ' M x\n'), (0, ''),
        (0, ''), (0, 'def5678'),
    ]
    rc, log = run(env, monkeypatch, [])
    assert rc == 0
    assert 'PUSHED' in log
    assert '7 commit(s) synced' in log


def test_a_failed_outer_commit_aborts(env, monkeypatch):
    ds, script, _n, _o, _w = env
    preflight_clean(script)
    script.queue += [
        (1, ''), (0, ''), (0, ''),
        (0, ''), (0, ''), (0, ''),
        (0, ''), (0, ''),
        (0, ''), (0, ''), (0, ''), (0, 'abc1234'),
        (0, ''), (0, ' M x\n'),
        (1, 'commit failed'),
    ]
    rc, log = run(env, monkeypatch, [])
    assert rc == 1
    assert 'outer commit failed' in log


def test_a_failed_nested_ff_aborts(env, monkeypatch):
    ds, script, _n, _o, _w = env
    preflight_clean(script)
    script.queue += [
        (1, ''), (0, ''), (0, ''),
        (0, ''), (0, ''), (0, ''),
        (0, ''), (0, ''),
        (0, ''),                        # checkout main
        (1, 'not a fast-forward'),      # merge --ff-only
    ]
    rc, log = run(env, monkeypatch, [])
    assert rc == 1
    assert 'fast-forward failed' in log


def test_a_branch_that_cannot_be_created_aborts(env, monkeypatch):
    ds, script, _n, _o, _w = env
    preflight_clean(script)
    script.queue += [
        (1, ''),                # rev-parse --verify: does not exist
        (1, 'branch exists'),   # checkout -b fails
    ]
    rc, log = run(env, monkeypatch, [])
    assert rc == 1
    assert 'cannot create' in log


def test_an_existing_dated_branch_is_deleted_first(env, monkeypatch):
    """An earlier aborted run leaves the branch behind; the bot must start the day fresh."""
    ds, script, _n, _o, _w = env
    preflight_clean(script)
    script.queue += [
        (0, ''),                # rev-parse --verify: it exists
        (0, ''),                # branch -D
        (0, ''),                # checkout -b
        (0, ''),                # sync_upstream ok
        (0, ''), (0, ''), (0, ''),
        (0, ''), (0, ''),
        (0, ''), (0, ''), (0, ''), (0, 'abc1234'),
        (0, ''), (0, ' M x\n'), (0, ''),
        (0, ''), (0, 'def5678'),
    ]
    rc, _log = run(env, monkeypatch, [])
    assert rc == 0
    assert 'branch' in script.keys() and '-D' in str(script.calls)
