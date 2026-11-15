#!/usr/bin/env python3
# P0.3 E2E smoke (ROADMAP.md): the unattended device round. Either the full flow (build_ohos.py --lib, pack entry,
# send, bm install - all through devpush.py --no-start, which this script calls and never modifies) or --no-install
# (start the app already on the device, the common quick check), then aa start, then watch the dmhost hilog for the
# boot markers and write a JSON verdict whose fields a human or a bot can read.
#   python dm/ohos/e2e_smoke.py                                   # full round against BOTW (01007EF00011E000)
#   python dm/ohos/e2e_smoke.py --game 01007EF00011E000 --wait-s 120
#   python dm/ohos/e2e_smoke.py --no-install                      # start what is installed, watch the log
#   python dm/ohos/e2e_smoke.py --self-test                       # no device: prove the parsing and verdict logic
# Verdict JSON (default dm/ohos/out/e2e_<timestamp>.json): {result, markers_seen, crash_line, warn_lines, log_tail, elapsed_s}
# plus context extras (game, target, wait_s, no_install, total_s, note).
# Exit code: 0 pass, 1 fail (a crash marker, or the window ended without frames), 2 device locked / offline.
# [RED] lines are warnings (warn_lines), never a failure: this backend logs informational fallbacks under [RED].
# Windows host quirks: run python with PYTHONUTF8=1 (the console is cp936); this script prints ASCII only but sets
# PYTHONUTF8 itself so the python children (devpush -> build_ohos) read non-cp936-safe paths.
import argparse, json, os, shutil, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))          # dm/ohos/
DM = os.path.dirname(HERE)                                  # dm/
BUNDLE = 'dev.eden_emu.ohos'
ABILITY = 'EntryAbility'
DEFAULT_GAME = '01007EF00011E000'                           # BOTW: the one game P0 plays
DEFAULT_TARGET = '192.168.2.158:42213'                      # the only test device (ROADMAP: one device until P3)
LOCK_ERROR = '10106102'                                     # aa start's error code while the screen is locked (measured)
TAIL_LINES = 40                                             # the verdict's log_tail size
os.environ.setdefault('PYTHONUTF8', '1')   # the children (devpush -> build_ohos) open game paths this console's page cannot name

# The boot contract (ROADMAP P0.3): a good boot logs "game thread started" (napi_bridge.c), then "nnosInitialize"
# (host/main.c's ">>> nnosInitialize"), then eventually a frame-activity line: "[longframe]" is what a real game
# logs per analyzed long frame (hle_nvn.c's frame accounting), while "frames presented" is the no-data self-test
# scene's counter (dm/ohos/demo_ohos.c); either satisfies the contract. Bad markers stop the round:
# "FATAL" (host/dm.h die()) and "[dm] CRASH sig=" (host/crash.c's reporter, routed to this tag on OHOS) mean the
# process is going down. "[RED]" is NOT a failure: it is the repo's diagnostic prefix and on this backend most of
# its lines are informational performance fallbacks (a memory pool that could not be zero-copy falls back to a GPU
# copy, "slow" is the word the source uses); such lines are collected as warnings in the verdict instead.
MARKERS_BOOT = ('game thread started', 'nnosInitialize', '[longframe]', 'frames presented')
MARKERS_CRASH = ('FATAL', '[dm] CRASH sig=')
MARKERS_WARN = ('[RED]',)   # recorded in the verdict's warn_lines; never fails the round on its own


def parse_args(argv):
    ap = argparse.ArgumentParser(description='P0.3 E2E smoke: devpush/aa start/watch the dmhost hilog -> JSON verdict '
                                              '(exit 0 pass, 1 fail, 2 device locked/offline).')
    ap.add_argument('--game', default=DEFAULT_GAME, help='title ID (default: %(default)s)')
    ap.add_argument('--wait-s', dest='wait_s', type=int, default=180,
                    help='seconds to watch hilog after the start (default: %(default)s; a re-started app skips staging '
                         'but the boot markers can trail by a minute when the shader cache is cold)')
    ap.add_argument('--no-install', dest='no_install', action='store_true',
                    help='skip build/pack/install and start the app already on the device (the quick check)')
    ap.add_argument('--out', default=os.path.join(HERE, 'out'), help='verdict JSON directory (default: dm/ohos/out)')
    ap.add_argument('--target', default=DEFAULT_TARGET, help='hdc target to wake and use (default: %(default)s)')
    ap.add_argument('--hdc', help='hdc.exe path (default: DevEco SDK discovery, as devpush.py)')
    ap.add_argument('--poll-s', dest='poll_s', type=int, default=5, help='seconds between hilog polls (default: %(default)s)')
    ap.add_argument('--wake-tries', dest='wake_tries', type=int, default=3,
                    help='hdc tconn attempts while the device is asleep (default: %(default)s)')
    ap.add_argument('--wake-gap-s', dest='wake_gap_s', type=int, default=10, help='seconds between wake attempts (default: %(default)s)')
    ap.add_argument('--self-test', dest='self_test', action='store_true',
                    help='no device: prove the argument parsing, the marker/merge logic and the verdict writer, exit 0')
    return ap.parse_args(argv)


def find_hdc(override=None):
    if override: return override
    for p in (os.environ.get('DEVECO_HOME'), r'E:\Program Files\Huawei\DevEco Studio',
              r'D:\dev-echo\DevEco Studio', r'C:\Program Files\Huawei\DevEco Studio'):
        if p:
            h = os.path.join(p, 'sdk', 'default', 'openharmony', 'toolchains', 'hdc.exe')
            if os.path.isfile(h): return h
    return None


def hdc_runner(path):
    def run(*args, timeout=180):
        r = subprocess.run([path, *args], capture_output=True, text=True, errors='replace', timeout=timeout)
        return r.returncode, (r.stdout or '') + (r.stderr or '')
    return run


# ---- device wake / connect --------------------------------------------------------------------------------------------------------------

def list_target(run):
    """One connected target from `hdc list targets`, or None. hdc prints the target per line ('[Empty]' when none)."""
    rc, out = run('list', 'targets')
    for ln in out.splitlines():
        ln = ln.strip()
        if not ln or 'Empty' in ln or ln.startswith('['): continue
        return ln
    return None


def ensure_device(run, target, tries, gap_s, log):
    """The device may be asleep: `hdc list targets` empty -> tconn the known address, retry a few times, give up."""
    for i in range(tries):
        t = list_target(run)
        if t:
            log('device: %s' % t)
            return t
        log('hdc has no target (try %d/%d): tconn %s' % (i + 1, tries, target))
        run('tconn', target)
        time.sleep(gap_s)
    t = list_target(run)
    if t: log('device: %s' % t)
    return t


def parse_aa_start(out):
    """aa start's answer: 'locked' (error 10106102, the screen lock refuses the start - do not retry), 'ok', 'refused'."""
    if LOCK_ERROR in out: return 'locked'
    if 'successfully' in out.lower(): return 'ok'
    return 'refused'


# ---- the log watch ----------------------------------------------------------------------------------------------------------------------

def merge_lines(old, new):
    """hilog -x dumps the whole buffer every poll, so new is normally old plus appended lines; after a buffer rotation
    the oldest fell out. Merge without duplicating the overlap, and never lose a line."""
    if not old: return list(new)
    if new[:len(old)] == old: return old + list(new[len(old):])
    for k in range(len(old), 0, -1):                    # rotation: the longest tail of old that new starts with
        if new[:k] == old[-k:]: return old + list(new[k:])
    return old + list(new)                              # no overlap at all: keep everything (a cleared buffer)


class BootWatch:
    """Collects the dmhost-tagged hilog lines, notes the markers and the first line that fails the round."""

    def __init__(self):
        self.lines = []        # every dmhost line collected, in order
        self.markers = []      # marker names in first-seen order (boot markers together)
        self.crash_line = None # the first line with a crash marker; None when the round failed only by timeout
        self.hard_crash = None # the first FATAL / CRASH line: the process is going down, stop watching
        self.warn_lines = []   # the [RED] diagnostic lines (informational fallbacks on this backend); never a verdict

    def poll(self, lines):
        self.lines = merge_lines(self.lines, lines)
        for ln in self.lines:
            for m in MARKERS_BOOT + MARKERS_CRASH:
                if m in ln and m not in self.markers: self.markers.append(m)
            if self.crash_line is None and any(m in ln for m in MARKERS_CRASH): self.crash_line = ln.strip()
            if self.hard_crash is None and any(m in ln for m in MARKERS_CRASH): self.hard_crash = ln.strip()
            if any(m in ln for m in MARKERS_WARN):
                w = ln.strip()
                if w not in self.warn_lines: self.warn_lines.append(w)

    def feed(self, text): self.poll(text.splitlines())   # convenience: a whole captured log in one string

    def saw(self, m): return m in self.markers

    def verdict(self):
        if self.crash_line is not None: return 'fail'   # a crash marker dominates even later frame activity
        if self.saw('game thread started') and (self.saw('[longframe]') or self.saw('frames presented')): return 'pass'
        return 'fail'                                   # the window ended without frame activity (a timeout fail)

    @property
    def log_tail(self): return [ln.rstrip() for ln in self.lines[-TAIL_LINES:]]


# ---- the verdict ------------------------------------------------------------------------------------------------------------------------

EXIT_CODES = {'pass': 0, 'fail': 1, 'device_locked': 2, 'offline': 2}


def exit_code(result): return EXIT_CODES.get(result, 1)


def make_verdict(result, watch, elapsed_s, **extra):
    v = {'result': result, 'markers_seen': list(watch.markers), 'crash_line': watch.crash_line, 'warn_lines': watch.warn_lines,
         'log_tail': watch.log_tail, 'elapsed_s': round(elapsed_s, 1)}
    v.update(extra)
    return v


def write_verdict(out_dir, data, name=None):
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, name or ('e2e_%s.json' % time.strftime('%Y%m%d_%H%M%S')))
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)
        f.write('\n')
    return path


def run_stream(cmd, env=None):
    """Run a child live (its output keeps flowing to ours) and return (exit code, the last 2000 chars) for the note."""
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors='replace', env=env)
    tail = []
    for ln in p.stdout:
        print('    ' + ln.rstrip())
        tail.append(ln)
    p.wait()
    return p.returncode, ''.join(tail)[-2000:]


# ---- the real device round -------------------------------------------------------------------------------------------------------------

def run_smoke(a):
    t_all = time.time()
    hdc = find_hdc(a.hdc)
    if not hdc: sys.exit('e2e_smoke: hdc.exe not found (pass --hdc <path> or set DEVECO_HOME)')
    run = hdc_runner(hdc)

    # 1. wake / connect the device (it may be asleep; a scheduled task on this PC wakes it every 5 min)
    t0 = time.time()
    dev = ensure_device(run, a.target, a.wake_tries, a.wake_gap_s, log=print)
    if not dev:
        v = make_verdict('offline', BootWatch(), t0 and time.time() - t0, game=a.game, target=a.target,
                         note='no device on hdc after %d tconn attempts' % a.wake_tries)
        path = write_verdict(a.out, v)
        print('VERDICT: OFFLINE - no device on hdc (%s) after %d tries: the phone is asleep or off' % (a.target, a.wake_tries))
        print('  the scheduled wake task on this PC retries every 5 min - run the smoke again once the device is awake')
        print('  verdict: %s' % path)
        return 2

    # 2. the full round builds and installs through devpush.py --no-start (build -> pack entry -> send -> bm install);
    #    --no-install starts what is already on the device
    if not a.no_install:
        print('[e2e] devpush: build_ohos.py --lib -> pack entry -> send -> bm install (no start)')
        rc, tail = run_stream([sys.executable, os.path.join(HERE, 'devpush.py'), '--game', a.game, '--no-start'])
        if rc:
            v = make_verdict('fail', BootWatch(), time.time() - t_all, game=a.game, target=dev,
                             note='devpush failed (exit %d):\n%s' % (rc, tail))
            path = write_verdict(a.out, v)
            print('VERDICT: FAIL - devpush failed (exit %d); see the note in the verdict JSON' % rc)
            print('  verdict: %s' % path)
            return 1
        time.sleep(3)   # the security scan rejects a start in its first seconds after an install (measured; mkhap waits 3 s too)

    # 3. a fresh boot: stop what may be running (a warm start logs nothing new), clear the buffer, start, do not retry the lock
    run('shell', 'aa', 'force-stop', BUNDLE)
    run('shell', 'hilog', '-r')
    rc, out = run('shell', 'aa', 'start', '-b', BUNDLE, '-a', ABILITY, timeout=180)
    print('[e2e] aa start: %s' % out.strip().replace('\n', ' ')[:160])
    st = parse_aa_start(out)
    if st == 'locked':
        v = make_verdict('device_locked', BootWatch(), 0.0, game=a.game, target=dev,
                         note='aa start refused with %s: the screen is locked' % LOCK_ERROR)
        path = write_verdict(a.out, v)
        print('VERDICT: DEVICE_LOCKED - unlock the phone')
        print('  verdict: %s' % path)
        return 2
    if st == 'refused':
        v = make_verdict('fail', BootWatch(), 0.0, game=a.game, target=dev,
                         note='aa start refused: %s' % out.strip()[:200])
        path = write_verdict(a.out, v)
        print('VERDICT: FAIL - aa start refused (not the lock error; see the verdict JSON)')
        print('  verdict: %s' % path)
        return 1

    # 4. watch the dmhost log for the boot markers
    watch = BootWatch()
    t0 = time.time()
    deadline = t0 + a.wait_s
    bad_polls = 0
    lost = False
    print('[e2e] watching hilog (tag dmhost) for %d s...' % a.wait_s)
    while True:
        remain = deadline - time.time()
        if remain <= 0: break
        time.sleep(min(a.poll_s, remain))
        rc, out = run('shell', 'hilog', '-x', '-T', 'dmhost', timeout=a.poll_s + 60)
        watch.poll(out.splitlines())          # an rc!=0 answer feeds its error text; it carries no marker
        bad_polls = bad_polls + 1 if rc else 0
        if watch.hard_crash is not None:
            print('[e2e] crash marker: %s' % watch.hard_crash[:160])
            break                             # the process is going down: nothing more will arrive
        if bad_polls >= 3:
            lost = True
            print('[e2e] hdc lost the device mid-run (%d failed polls)' % bad_polls)
            break
    elapsed = time.time() - t0
    result = watch.verdict()
    v = make_verdict(result, watch, elapsed, game=a.game, target=dev, wait_s=a.wait_s, no_install=a.no_install,
                     total_s=round(time.time() - t_all, 1),
                     note='hdc lost the device mid-run' if lost else None)
    path = write_verdict(a.out, v)
    print('[e2e] markers: %s' % (', '.join(watch.markers) or 'none'))
    if watch.crash_line: print('[e2e] crash line: %s' % watch.crash_line[:160])
    why = ('the boot reached frame activity' if result == 'pass'
           else ('a crash marker stopped the round' if watch.crash_line else 'no frame activity in the window'))
    print('VERDICT: %s - %s (%.0f s; exit %d)' % ('PASS' if result == 'pass' else 'FAIL', why, elapsed, exit_code(result)))
    print('  verdict: %s' % path)
    return exit_code(result)


# ---- the self-test: no device, fake streams through the same code ----------------------------------------------------------------------

def self_test(a):
    fails = []

    def check(name, cond, detail=''):
        print(('  ok   ' if cond else '  FAIL ') + name + (('  [%r]' % (detail,)) if not cond else ''))
        if not cond: fails.append(name)

    def hl(msg, seq=[0]):    # a plausible `hilog -x -T dmhost` line around the message
        seq[0] += 1
        return '11-15 14:20:%02d.%03d  1234  567 D 0201/dmhost 00%04d: %s' % (41 + seq[0] // 1000, seq[0] % 1000, seq[0], msg)

    print('e2e_smoke self-test (no device)')
    # 1. argument parsing: this run itself proves --self-test parses; check the defaults and an explicit set
    check('args: --self-test parsed', a.self_test)
    check('args: defaults', a.game == DEFAULT_GAME and a.wait_s == 180 and not a.no_install and a.out == os.path.join(HERE, 'out')
          and a.target == DEFAULT_TARGET and a.poll_s == 5 and a.wake_tries == 3 and a.wake_gap_s == 10)
    ns = parse_args(['--game', '0100A6301214E000', '--wait-s', '7', '--no-install', '--out', 'X',
                     '--target', '1.2.3.4:5', '--poll-s', '2', '--wake-tries', '4', '--wake-gap-s', '0'])
    check('args: explicit flags', ns.game == '0100A6301214E000' and ns.wait_s == 7 and ns.no_install and ns.out == 'X'
          and ns.target == '1.2.3.4:5' and ns.poll_s == 2 and ns.wake_tries == 4 and ns.wake_gap_s == 0)

    # 2. a good boot: the three markers in order -> pass, exit 0
    w = BootWatch()
    w.feed('\n'.join([hl('napi: libdmhost loaded'), hl('napi: game thread started (root=/data/storage/el2/base/files)'),
                      hl('>>> nnosInitialize'), hl('front: surface 123 is up (1x1)'), hl('[demo] 120 frames presented')]))
    check('watch: good boot -> pass', w.verdict() == 'pass', w.verdict())
    check('watch: markers in first-seen order',
          w.markers == ['game thread started', 'nnosInitialize', 'frames presented'], w.markers)
    check('watch: exit code 0', exit_code(w.verdict()) == 0)

    # 3. a crash: the crash reporter's line -> fail with crash_line set
    w = BootWatch()
    w.feed('\n'.join([hl('napi: game thread started (root=/data/x)'), hl('>>> nnosInitialize'),
                      hl('[dm] CRASH sig=11 addr=0 pc=main+0x3409a78 lr=main+0x1')]))
    check('watch: crash -> fail', w.verdict() == 'fail')
    check('watch: crash_line captured', 'CRASH sig=11' in (w.crash_line or ''), w.crash_line)

    # 4. FATAL alone -> fail
    w = BootWatch()
    w.feed(hl('FATAL the game files are missing'))
    check('watch: FATAL -> fail', w.verdict() == 'fail' and 'FATAL' in w.markers)

    # 5. a [RED] diagnostic line is a warning, never a failure (the backend's zero-copy fallback logs [RED] "slow" while the game runs)
    w = BootWatch()
    w.feed('\n'.join([hl('napi: game thread started (root=/data/x)'), hl('[RED] vi layer crop 0,0 1x1 does not fit'),
                      hl('[demo] 60 frames presented')]))
    check('watch: [RED] + frames -> pass with a warning', w.verdict() == 'pass' and 'frames presented' in w.markers and len(w.warn_lines) == 1 and w.crash_line is None)

    # 6. timeout: the boot markers but no frames -> fail
    w = BootWatch()
    w.feed('\n'.join([hl('napi: game thread started (root=/data/x)'), hl('>>> nnosInitialize')]))
    check('watch: no frames -> fail', w.verdict() == 'fail' and w.crash_line is None)

    # 7. poll merge: an overlapping poll adds only the new lines; a buffer rotation keeps the fallen-out lines
    a1 = [hl('napi: game thread started (root=/data/x)'), hl('>>> nnosInitialize'), hl('front: surface 7 is up (1x1)')]
    w = BootWatch()
    w.poll(a1)
    w.poll(a1 + [hl('[demo] 60 frames presented')])
    check('merge: overlapping poll adds only the new line', len(w.lines) == 4, len(w.lines))
    w = BootWatch()
    w.poll(a1)
    rotated = a1[1:] + [hl('[demo] 60 frames presented')]     # a1[0] fell out of the buffer
    w.poll(rotated)
    check('merge: rotation keeps the fallen-out line', len(w.lines) == 4 and w.lines[0] == a1[0] and w.lines[3] == rotated[-1],
          w.lines)

    # 8. aa start parsing: the lock error, the success word, anything else refused
    check('aa start: locked', parse_aa_start('start ability failed, error code: %s' % LOCK_ERROR) == 'locked')
    check('aa start: ok', parse_aa_start('start ability successfully.') == 'ok')
    check('aa start: refused', parse_aa_start('error: install not found') == 'refused')

    # 9. device wake: already connected (no tconn), tconn-then-connected, never (offline)
    calls, state = [], {'targets': '[Empty]'}

    def fake_run(*args, **kw):
        calls.append(args)
        if args[:2] == ('list', 'targets'): return 0, state['targets']
        if args[:1] == ('tconn',): return 0, 'Connect OK'
        return 0, ''

    state['targets'] = DEFAULT_TARGET
    check('wake: connected target returned, no tconn',
          ensure_device(fake_run, DEFAULT_TARGET, 3, 0, log=lambda s: None) == DEFAULT_TARGET
          and ('tconn', DEFAULT_TARGET) not in calls)
    calls.clear()
    state['targets'] = '[Empty]'
    t = ensure_device(fake_run, DEFAULT_TARGET, 3, 0, log=lambda s: None)   # the fake never flips: stays offline
    check('wake: offline after the tries',
          t is None and calls.count(('tconn', DEFAULT_TARGET)) == 3 and calls[0][:2] == ('list', 'targets'))
    # tconn wakes it: flip the answer on the tconn call
    calls.clear()
    state['targets'] = '[Empty]'

    def fake_run2(*args, **kw):
        if args[:1] == ('tconn',):
            state['targets'] = DEFAULT_TARGET
        return fake_run(*args, **kw)

    check('wake: tconn then connected', ensure_device(fake_run2, DEFAULT_TARGET, 3, 0, log=lambda s: None) == DEFAULT_TARGET)

    # 10. the verdict writer: all five fields land in the JSON and read back
    tmp = tempfile.mkdtemp(prefix='e2e_selftest_')
    try:
        w = BootWatch()
        w.feed('\n'.join([hl('napi: game thread started (root=/data/x)'), hl('>>> nnosInitialize'), hl('[demo] 120 frames presented')]))
        v = make_verdict(w.verdict(), w, 12.34, game='TESTGAME', target='1.2.3.4:5')
        p = write_verdict(tmp, v, name='e2e_selftest.json')
        back = json.load(open(p, encoding='utf-8'))
        check('verdict: the five fields', set(('result', 'markers_seen', 'crash_line', 'warn_lines', 'log_tail', 'elapsed_s')) <= set(back), sorted(back))
        check('verdict: values', back['result'] == 'pass' and back['markers_seen'] == w.markers and back['crash_line'] is None
              and back['elapsed_s'] == 12.3 and len(back['log_tail']) == 3, back)
        check('verdict: exit codes', exit_code('pass') == 0 and exit_code('fail') == 1
              and exit_code('device_locked') == 2 and exit_code('offline') == 2)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if fails:
        print('SELF-TEST FAILED: %s' % ', '.join(fails))
        return 1
    print('SELF-TEST PASSED')
    return 0


def main(argv):
    a = parse_args(argv)
    if a.self_test: return self_test(a)
    return run_smoke(a)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
