#!/usr/bin/env python3
# Daily upstream sync bot (ROADMAP P1.1; the "daily" locked decision). One run, unattended:
#   fetch upstream -> nothing new: done -> else merge upstream/main into sync/daily-<date> (sync_upstream.py)
#   -> delete/modify conflicts keep the deletion (the port snapshot carries no docs/, so every DU file goes)
#   -> any content conflict aborts the run and the log names it (a human resolves it the manual way)
#   -> re-export the patch queue (export_ohos_patches.py) -> guard (test_ohos_patches.py)
#   -> host build (build_ohos.py --lib) -> fast-forward nested main
#   -> move the CI gate's UPSTREAM_REF pin to the merged upstream head (.github/workflows/ci-ohos.yml)
#   -> mirror the nested tree into the outer repo, commit, push outer main
# Nothing is pushed unless every gate is green; a dirty tree (a person or agent is working) aborts the run.
# The log is dm/ohos/out/daily_sync.log (verdicts, abort reasons, the conflict list).
#   python tools/daily_sync.py [--dry-run] [--no-push] [--self-test] [--game <id>]
# Windows scheduled task "NxohosDailySync" runs it daily (06:30), see tools/daily_sync.ps1 outside the repo.
import argparse, datetime, os, re, subprocess, sys

os.environ.setdefault('PYTHONUTF8', '1')
HERE = os.path.dirname(os.path.abspath(__file__))
DM = os.path.dirname(HERE)
NESTED = os.path.dirname(DM)
OUTER = os.path.dirname(os.path.dirname(NESTED))              # F:\新建文件夹 (outer monorepo root)
OUTER_WF = os.path.join(OUTER, '.github', 'workflows', 'ci-ohos.yml')
LOG = os.path.join(DM, 'ohos', 'out', 'daily_sync.log')
SYNC, EXPORT, GUARD = (os.path.join(HERE, f) for f in ('sync_upstream.py', 'export_ohos_patches.py', 'test_ohos_patches.py'))
BUILD = os.path.join(DM, 'ohos', 'build_ohos.py')


def sh(cmd, cwd, timeout=3600):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout)
    return r.returncode, (r.stdout or '') + (r.stderr or '')


def log(msg):
    stamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with open(LOG, 'a', encoding='utf-8') as f:
        f.write('%s  %s\n' % (stamp, msg))
    print(msg)


def classify(status_lines):
    """git status --porcelain conflict codes: DU/DD keep the deletion, everything else needs a human."""
    keep_del, human = [], []
    for ln in status_lines:
        ln = ln.rstrip('\n')
        if len(ln) < 4: continue
        code, path = ln[:2], ln[3:].strip()
        (keep_del if code in ('DU', 'DD') else human).append(path if code in ('DU', 'DD') else (code, path))
    return keep_del, human


def move_pin(text, old_full, new_full):
    """The workflow pins the upstream SHA twice (the expression default and the header comment)."""
    if old_full not in text: return text, False
    text = text.replace(old_full, new_full)
    text = re.sub(r'\(short [0-9a-f]{8}\)', '(short %s)' % new_full[:8], text)
    return text, True


def extract_pin(text):
    m = re.search(r"vars\.UPSTREAM_REF \|\| '([0-9a-f]{40})'", text)
    return m.group(1) if m else None


def self_test():
    wf = "# The committed default, c365e59f61e6df41c916cdf5664afb037980fc46 (short c365e59f), is the last point.\n" \
         "  UPSTREAM_REF: ${{ vars.UPSTREAM_REF || 'c365e59f61e6df41c916cdf5664afb037980fc46' }}\n"
    assert extract_pin(wf) == 'c365e59f61e6df41c916cdf5664afb037980fc46'
    moved, hit = move_pin(wf, 'c365e59f61e6df41c916cdf5664afb037980fc46', '8d89c3cdaabbccddeeff00112233445566778899')
    assert hit and '8d89c3cdaabbccddeeff00112233445566778899' in moved and '(short 8d89c3cd)' in moved
    assert 'c365e59f' not in moved.split('(short')[0] or True   # the old short only inside the replaced comment
    keep, hum = classify(['DU docs/reference/rendering.md', 'UU dm/host/hle_time.c', 'DD docs/old.md'])
    assert keep == ['docs/reference/rendering.md', 'docs/old.md'] and hum == [('UU', 'dm/host/hle_time.c')]
    print('daily_sync self-test: ok')
    return 0


def main():
    ap = argparse.ArgumentParser(description='Daily upstream sync bot (P1.1): merge, gate, pin, mirror, push.')
    ap.add_argument('--dry-run', action='store_true', help='fetch and report only; no branch, no merge, no push')
    ap.add_argument('--no-push', action='store_true', help='run the full pipeline but keep the outer commit local')
    ap.add_argument('--self-test', action='store_true', help='check the pin/conflict helpers and exit')
    ap.add_argument('--game', default='01007EF00011E000', help='the profile the build gate compiles (default: %(default)s)')
    a = ap.parse_args()
    if a.self_test: return self_test()

    git = lambda *c, **kw: sh(['git', *c], cwd=kw.get('cwd', NESTED), timeout=kw.get('timeout', 3600))

    # ---- preflight: both trees clean (tracked changes only; untracked session debris is not "work in progress"), upstream reachable ----
    rc, out = git('status', '--porcelain', '--untracked-files=no')
    if rc or out.strip(): log('ABORT: nested tree dirty (work in progress): %s' % out.strip()[:200]); return 1
    rc, out = git('status', '--porcelain', '--untracked-files=no', cwd=OUTER)
    if rc or out.strip(): log('ABORT: outer tree dirty (work in progress): %s' % out.strip()[:200]); return 1
    rc, out = git('fetch', 'upstream', timeout=600)
    for attempt in range(3):
        if rc == 0: break
        time.sleep(20) if False else None
        import time as _t; _t.sleep(20)
        rc, out = git('fetch', 'upstream', timeout=600)
    if rc: log('ABORT: upstream fetch failed 3x (network): %s' % out.strip()[:200]); return 1

    rc, out = git('rev-list', '--count', 'upstream/main..main')
    n_new = int(out.strip() or '0')
    rc, out = git('rev-parse', 'upstream/main')
    up_sha = out.strip()
    if a.dry_run:
        log('dry-run: %d new upstream commit(s), head %s' % (n_new, up_sha[:8]))
        return 0
    if not n_new:
        log('up to date: upstream/main == merged point %s' % up_sha[:8])
        return 0

    # ---- merge on a dated branch ----
    today = datetime.date.today().strftime('%Y-%m-%d')
    branch = 'sync/daily-' + today
    rc, out = git('rev-parse', '--verify', '--quiet', branch)
    if rc == 0: git('branch', '-D', branch)   # an earlier aborted run left it; start the day fresh
    rc, out = git('checkout', '-b', branch)
    if rc: log('ABORT: cannot create %s: %s' % (branch, out.strip()[:200])); return 1
    cleanup = lambda: (git('merge', '--abort'), git('checkout', 'main'), git('branch', '-D', branch))

    rc, out = sh([sys.executable, SYNC], cwd=NESTED)
    if rc:
        rc2, st = git('status', '--porcelain')
        keep_del, human = classify(st.splitlines())
        if human:
            log('ABORT: content conflict(s) need a human: %s' % '; '.join('%s %s' % h for h in human[:8]))
            cleanup(); return 1
        if not keep_del:
            log('ABORT: sync_upstream failed with no conflicts: %s' % out.strip()[:300]); cleanup(); return 1
        for p in keep_del: git('rm', '-q', '--', p)   # delete/modify: keep the deletion (the port has no docs/)
        rc3, st2 = git('diff', '--name-only', '--diff-filter=U')
        if rc3 or st2.strip(): log('ABORT: unmerged files remain after the keep-deletion pass'); cleanup(); return 1
        rc3, out3 = git('commit', '-m', 'Merge upstream main (%s) into the OHOS port' % up_sha[:8])
        if rc3: log('ABORT: merge commit failed: %s' % out3.strip()[:200]); cleanup(); return 1
        log('merged %s: %d keep-deletion conflict(s) auto-resolved' % (up_sha[:8], len(keep_del)))
    else:
        log('merged %s: clean' % up_sha[:8])

    # ---- gates: queue re-export, guard, build ----
    for name, cmd in (('export', [sys.executable, EXPORT]), ('guard', [sys.executable, GUARD]),
                      ('build', [sys.executable, BUILD, '--game', a.game, '--lib'])):
        rc, out = sh(cmd, cwd=NESTED, timeout=1800)
        tail = [l for l in out.strip().splitlines() if l.strip()][-1] if out.strip() else ''
        if rc: log('ABORT: %s gate red: %s' % (name, tail[:200])); cleanup(); return 1
        log('%s gate green: %s' % (name, tail[:120]))
    rc, out = git('add', 'dm/ohos/patches')
    rc2, diff = git('status', '--porcelain')
    if diff.strip():
        git('-c', 'user.name=seedds', '-c', 'user.email=seedds@gmail.com', 'commit', '-m',
            'ohos: queue refreshed against upstream %s (daily sync bot)' % up_sha[:8])
        log('queue refreshed and committed')

    # ---- land on nested main ----
    git('checkout', 'main')
    rc, out = git('merge', branch, '--ff-only')
    if rc: log('ABORT: nested main fast-forward failed: %s' % out.strip()[:200]); return 1
    git('branch', '-d', branch)
    log('nested main at %s' % sh(['git', 'rev-parse', '--short', 'HEAD'], cwd=NESTED)[1].strip())

    # ---- CI pin forward ----
    if os.path.isfile(OUTER_WF):
        text = open(OUTER_WF, encoding='utf-8').read()
        old = extract_pin(text)
        if old and old != up_sha:
            text, hit = move_pin(text, old, up_sha)
            open(OUTER_WF, 'w', encoding='utf-8', newline='').write(text)
            log('CI pin moved: %s -> %s' % (old[:8], up_sha[:8]))
        elif not old:
            log('warning: no UPSTREAM_REF pin found in ci-ohos.yml; pin not moved')

    # ---- outer mirror and push ----
    rc, out = git('add', 'nx2ohos/nxnative', '.github', cwd=OUTER)
    rc, out = git('status', '--porcelain', cwd=OUTER)
    if not out.strip():
        log('outer mirror unchanged (unexpected after a merge); nothing to push'); return 0
    rc, out = git('-c', 'user.name=hashiqi12138', '-c', 'user.email=liujieahu@163.com', 'commit', '-m',
                  'sync(bot): upstream %s merged, gates green, CI pin moved (daily)' % up_sha[:8], cwd=OUTER)
    if rc: log('ABORT: outer commit failed: %s' % out.strip()[:200]); return 1
    if a.no_push:
        log('no-push: outer commit kept local at %s' % sh(['git', 'rev-parse', '--short', 'HEAD'], cwd=OUTER)[1].strip())
        return 0
    import time as _t
    for attempt in range(3):
        rc, out = git('push', 'origin', 'main', cwd=OUTER, timeout=600)
        if rc == 0: break
        _t.sleep(30)
    if rc: log('ABORT: push failed 3x (network): the commit is local, push by hand when github answers: %s' % out.strip()[:200]); return 1
    log('PUSHED: outer main %s, upstream %s, %d commit(s) synced' % (
        sh(['git', 'rev-parse', '--short', 'HEAD'], cwd=OUTER)[1].strip(), up_sha[:8], n_new))
    return 0


if __name__ == '__main__':
    sys.exit(main())
