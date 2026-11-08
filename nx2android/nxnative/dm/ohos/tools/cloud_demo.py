#!/usr/bin/env python3
# Runs the dmhost HAP on the AGC cloud device (Pura 70 Pro+) and reads back what the native module logged. It reuses the CDP plumbing that
# the M0 probe established (ohos-port/tools/verify/run_cloud_probe.py): the same install card, the same "clear the log before starting"
# order, the same aa start retry while the security scan runs.
#   python ohos/tools/cloud_demo.py [--wait 25] [--hap <path>]
import argparse, os, re, sys, time
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # dm/ohos/tools -> dm
WORKSPACE = os.path.dirname(os.path.dirname(os.path.dirname(ROOT)))                   # dm -> xnative -> x2android -> the workspace
sys.path.insert(0, os.path.join(WORKSPACE, 'ohos-port', 'tools', 'verify'))
import run_cloud_probe as R   # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument('--wait', type=int, default=25, help='seconds to watch the log after the start')
ap.add_argument('--hap', default=os.path.join(ROOT, 'ohos', 'out', 'dmhost-signed.hap'))
ap.add_argument('--port', type=int, default=9222)
ap.add_argument('--apply', action='store_true', help='apply for a device when no session is running')
ap.add_argument('--shot', default='', help='also save a screenshot of the device canvas here')
ap.add_argument('--start-only', action='store_true',
                help='skip the install and just start the app already on the device')
ap.add_argument('--tag', default='dmhost',
                help="hilog tag to read: the native module's when omitted, JSApp for the page's console.info lines")
ap.add_argument('--pid-log', action='store_true',
                help='read every line of this app instead of one tag (hilog -P), which is where the page\'s console.info goes')
ap.add_argument('--sh', default='',
                help='run this one hdc shell command after the start (e.g. ls of the app sandbox) and print its output')
a = ap.parse_args()
if not os.path.isfile(a.hap): sys.exit(f'cloud_demo.py: {a.hap} is missing — run ohos/mkhap.py first')
R.HAP = a.hap
os.makedirs(R.DL_DIR, exist_ok=True)
r = R.Runner(a.port)
r.tab('单机调试'); time.sleep(2)
if '使用优惠时长中' not in r.text():
    if not a.apply: sys.exit('cloud_demo.py: no debug session — apply for a device in the cloud debug page, or pass --apply')
    if not r.apply_device(): sys.exit('cloud_demo.py: no device available')
print('[1] reusing the current session')
r.dismiss_privacy()
if not a.start_only:
    r.install_hap()
r.start_logging()


def shell(cmd, wait=5):
    """Run one hdc shell command from the '控制参数 → Shell脚本' panel.
    Runner.shell assumed the panel was already open; once the console kept it
    collapsed it answered NOBTN and every aa start silently did nothing, so this
    clicks the nav item by its text and retries before giving up."""
    for attempt in range(3):
        r.confirm_all()   # the console puts a '确定' dialog (session renewal, privacy hint) over the panel now and then
        r.tab('控制参数'); time.sleep(1.5)
        r.confirm_all()
        # only .nav-item: matching the wrapping divs too clicks a container whose
        # handler keeps the panel closed (measured: nav=OK, textarea NOTA)
        nav = r.fr("""
          const el=[...d.querySelectorAll('.nav-item')].find(n=>
            (n.innerText||'').trim()==='Shell脚本');
          if(!el) return 'NONAV';
          el.click(); return 'OK';""")
        time.sleep(1.5 + attempt)
        res = r.fr("""
          const ta=[...d.querySelectorAll('textarea')].find(n=>
            n.placeholder && n.placeholder.indexOf('shell')>-1);
          if(!ta) return 'NOTA';
          if(ta.getBoundingClientRect().width<300) return 'HIDDEN';
          const setter=Object.getOwnPropertyDescriptor(w.HTMLTextAreaElement.prototype,'value').set;
          setter.call(ta,%s); ta.dispatchEvent(new w.Event('input',{bubbles:true}));
          const bs=[...d.querySelectorAll('button')].filter(n=>
            (n.innerText||'').trim()==='执行' && n.offsetWidth>0);
          if(!bs.length) return 'NOBTN';
          bs[bs.length-1].click(); return 'run';""" % ("'" + cmd.replace("'", "\\'") + "'"))
        if res == 'run':
            time.sleep(wait)
            # the panel prints '执行命令：' then the command's stdout; only that
            # wrapper has it (a body-wide slice picked up '安装成功' from another tab)
            return r.fr("const e=d.querySelector('.clipboard-sync-wrapper');"
                        " return e?e.innerText:'';") or ''
        print('     shell(%s): nav=%s run=%s' % (cmd, nav, res))
    return ''


def launch(budget=180):
    """aa start until the device is done with its security scan.
    Installing puts the cloud device into '应用正在安装中' and aa start answers
    'start ability successfully' while nothing starts (measured: pidof empty)."""
    shell('aa force-stop dev.eden_emu.ohos', 3)
    t0 = time.time()
    while time.time() - t0 < budget:
        print('     aa start:', shell('aa start -b dev.eden_emu.ohos -a EntryAbility', 4).strip().replace('\n', ' ')[:150])
        out = shell('pidof dev.eden_emu.ohos', 3) or ''
        m = re.findall(r'\b\d{2,7}\b', out)
        pid = m[-1] if m else ''
        print('     aa start -> pid %r (%r)' % (pid, out.strip().replace('\n', ' ')[:80]))
        if pid.isdigit():
            return True
        time.sleep(15)
    return False


def hilog(tag='dmhost'):
    """Read the log from the device, not from the console's HiLog tab: on this
    console the tab answers '暂无数据' for a keyword that the device's own
    hilog buffer holds (measured), while `hilog -T <tag> -x` returns it.
    The tag must stay inside 0x0000-0xFFFF's domain and carry a tag the app
    sets (ohos/ohos_log.c: domain 0x0201, tag dmhost)."""
    return shell('hilog -T %s -x' % tag, 6) or ''


if not a.start_only:
    # Prove the device really took *this* HAP: an install the console skips (same
    # versionCode, or a card that was not ready) leaves the old native library
    # running, and the crash then looks exactly unchanged.
    import json, zipfile
    with zipfile.ZipFile(R.HAP) as z:
        want = json.loads(z.read('module.json'))['app']['versionCode']
    m = re.search(r'versionCode[^0-9]*(\d+)', shell('bm dump -n dev.eden_emu.ohos', 6))
    got = m.group(1) if m else '?'
    print('[4] versionCode on device %s, hap %s%s' % (got, want, '' if got == str(want) else '  <-- MISMATCH: the old build is running'))

# the crash handler's backtrace frames name libvulkan / the driver, and those lines only carry our '[demo] #N' prefix: keep the tag as it is
started = launch()
if not started:
    print('cloud_demo.py: the device never ran the app (install/scan window?)')
print(f'[7] watching for {a.wait} s')
time.sleep(a.wait)
if a.shot:
    try:
        r.shot_canvas(a.shot)
        print('screenshot -> ' + a.shot)
    except Exception as e:
        print('screenshot failed: ' + str(e))
if a.sh:
    print('--- hdc shell %s ---' % a.sh)
    print(shell(a.sh, 5) or '(empty)')
pid = ''
if a.pid_log:
    ids = re.findall(r'\b\d{2,7}\b', shell('pidof dev.eden_emu.ohos', 3))
    pid = ids[-1] if ids else ''
panel = (shell('hilog -P %s -x' % pid, 6) or '') if pid else hilog(a.tag)
lines = [l for l in panel.split('\n') if l.strip()]
# a shared tag (JSAPP) carries every process on the device: keep ours, and only fall back to the raw tail when none matched
ours = [l for l in lines if 'dev.eden_emu.ohos' in l]
print('--- hilog %s: %d lines (%d from the app) ---' % ('-P (this app)' if a.pid_log else '-T ' + a.tag, len(lines), len(ours)))
# the frame counter buries everything else in a long session: show the other lines, and only fall back to the raw tail when there are none
quiet = [l for l in (ours or lines) if 'frames presented' not in l]
# the frame counter is one line a second and buries everything else, but whether it moves is the whole point of the demo: report it in the header
counts = [int(m.group(1)) for l in (ours or lines) for m in [re.search(r'(\d+) frames presented', l)] if m]   # ours: a shared tag carries every process on the device
print('   frame counter: %s' % (f'{max(counts)} frames presented' if counts else 'no frame reported yet'))
print('\n'.join((quiet or lines)[-40:]) if lines else '(empty)')
