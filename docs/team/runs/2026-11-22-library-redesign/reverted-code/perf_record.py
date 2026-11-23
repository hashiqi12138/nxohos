#!/usr/bin/env python3
"""Put a measurement and a picture where the library can read them: <game root>/perf.json and <game root>/shots/.

The app writes device records itself while a game runs (pages/GamePerfSampler.ets). This script is the other half,
for the facts the app cannot see:

  * the GPU / replay / descriptor-set split, which only dmhost's hilog timing report carries (measure.py reads it);
  * a picture of a title that presents outside the app's XComponent, where createPixelMapFromSurface has nothing to
    read: `snapshot_display` on the device captures the panel itself.

Both write the SAME record shape (pages/GamePerf.ets, schema 1), into the SAME place: the game's own folder, at its
root - not under RomFS/ mods/ aoc/, because an import removes exactly those three (PackImport.ets:135) and a
measurement must survive a pack update.

    # one record from a measure.py run, into the pack tree that is about to be packed
    python dm/ohos/perf_record.py from-measure --measure perf.json --root <pack dir> --scene field

    # a cover from the device's panel, while the game runs
    python dm/ohos/perf_record.py shot --root <pack dir> --scene field --kind cover

    # what the library will show, without the app
    python dm/ohos/perf_record.py show --root <pack dir>

The root must be a pack dir (it holds nx2a-data.json); its title ID is the join key, so it is never guessed from the
folder name.
"""
import argparse
import datetime
import json
import os
import subprocess
import sys

HDC_DEFAULT = r'E:\Program Files\Huawei\DevEco Studio\sdk\default\openharmony\toolchains\hdc.exe'
BUNDLE = 'dev.eden_emu.ohos'
SCHEMA = 1
PERF_FILE = 'perf.json'
MEDIA_FILE = 'media.json'


def hdc_path(arg):
    if arg:
        return arg
    if os.path.isfile(HDC_DEFAULT):
        return HDC_DEFAULT
    for base in (os.environ.get('DEVECO_SDK_HOME', ''), os.environ.get('HOS_SDK_HOME', '')):
        cand = os.path.join(base, 'default', 'openharmony', 'toolchains', 'hdc.exe')
        if base and os.path.isfile(cand):
            return cand
    sys.exit('perf_record: hdc.exe not found; pass --hdc')


def pack_title(root):
    """The title ID the pack itself declares: the one field the library joins on."""
    p = os.path.join(root, 'nx2a-data.json')
    try:
        with open(p, 'r', encoding='utf-8') as f:
            j = json.load(f)
    except OSError:
        sys.exit('perf_record: %s has no nx2a-data.json - point --root at a pack dir' % root)
    t = str(j.get('title_id') or '').strip().upper()
    if len(t) != 16:
        sys.exit('perf_record: nx2a-data.json names no 16-hex title_id (%r)' % t)
    return t, str(j.get('display') or j.get('version') or '')


def pack_stamp(root):
    try:
        with open(os.path.join(root, '.stamp'), 'r', encoding='utf-8') as f:
            return f.read().strip()
    except OSError:
        return ''


def iso_now():
    return datetime.datetime.now().astimezone().replace(microsecond=0).isoformat()


def load_json(path, default):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, obj):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def device_of(hdc, target):
    """The device a record was measured on: the same three keys the app writes (GamePerf.thisDevice)."""
    def param(name):
        p = subprocess.run([hdc, '-t', target, 'shell', 'param get ' + name],
                           capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30)
        return (p.stdout or '').strip()
    model = param('const.product.model')
    market = param('const.product.name')
    api = 0
    try:
        api = int(param('const.ohos.apiversion') or 0)
    except ValueError:
        api = 0
    w, h = 0, 0
    p = subprocess.run([hdc, '-t', target, 'shell', 'hidumper -s RenderService -a screen'],
                       capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30)
    for line in (p.stdout or '').splitlines():
        if 'current screen' in line.lower() or 'width' in line.lower():
            nums = [int(x) for x in line.replace('x', ' ').replace(',', ' ').split() if x.isdigit()]
            if len(nums) >= 2 and nums[0] > 200 and nums[1] > 200:
                w, h = nums[0], nums[1]
                break
    return {'model': model, 'market': market, 'soc': '', 'api': api, 'width': w, 'height': h}


def cmd_from_measure(a):
    root = os.path.abspath(a.root)
    title, version = pack_title(root)
    m = load_json(a.measure, None)
    if not m or 'summary' not in m:
        sys.exit('perf_record: %s is not a measure.py --json output (no "summary")' % a.measure)
    s = m['summary']
    frame = float(s.get('frame_ms') or 0)
    if frame <= 0:
        sys.exit('perf_record: the measure has no frame_ms; nothing to record')
    dev = {'model': a.model, 'market': '', 'soc': '', 'api': 0, 'width': 0, 'height': 0}
    if a.hdc or os.path.isfile(HDC_DEFAULT):
        try:
            dev = device_of(hdc_path(a.hdc), a.target)
        except (OSError, subprocess.SubprocessError):
            pass
    rec = {
        'schema': SCHEMA,
        'titleId': title,
        'source': 'porter',
        'scene': a.scene,
        'device': dev,
        'fpsP50': round(1000.0 / frame),
        'frameMsP50': round(frame, 2),
        'gpuMsP50': round(float(s.get('gpu_ms') or -1), 2),
        'replayMsP50': round(float(s.get('replay_ms') or -1), 2),
        'setsMsP50': round(float(s.get('sets_ms') or -1), 2),
        'frames': int(round(float(s.get('frames') or 0) * int(m.get('pairs') or 1))),
        'capturedAt': iso_now(),
        'appVersion': version,
        'stamp': pack_stamp(root),
    }
    path = os.path.join(root, PERF_FILE)
    cur = load_json(path, {'schema': SCHEMA, 'titleId': title, 'records': []})
    cur['titleId'] = title
    cur.setdefault('records', []).append(rec)
    cur['records'] = cur['records'][-40:]
    save_json(path, cur)
    print('perf_record: %s  %s  %.1f ms  %d fps  frames %d' % (path, a.scene, rec['frameMsP50'], rec['fpsP50'], rec['frames']))
    return 0


def cmd_shot(a):
    root = os.path.abspath(a.root)
    title, _ = pack_title(root)
    hdc = hdc_path(a.hdc)
    remote = '/data/local/tmp/perf_%d.jpeg' % os.getpid()
    p = subprocess.run([hdc, '-t', a.target, 'shell', 'snapshot_display -f ' + remote],
                       capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=120)
    if p.returncode != 0:
        sys.exit('perf_record: snapshot_display failed: %s%s' % (p.stdout or '', p.stderr or ''))
    shots = os.path.join(root, 'shots')
    os.makedirs(shots, exist_ok=True)
    at = datetime.datetime.now()
    rel = 'shots/%s_%s.jpeg' % (a.scene, at.strftime('%Y%m%dT%H%M%S'))
    dst = os.path.join(root, rel.replace('/', os.sep))
    r = subprocess.run([hdc, '-t', a.target, 'file recv', remote, dst],
                       capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=300)
    subprocess.run([hdc, '-t', a.target, 'shell', 'rm -f ' + remote], capture_output=True, timeout=30)
    if r.returncode != 0 or not os.path.isfile(dst):
        sys.exit('perf_record: could not pull the shot: %s%s' % (r.stdout or '', r.stderr or ''))
    if a.kind == 'cover':
        import shutil
        shutil.copyfile(dst, os.path.join(root, 'cover.jpg'))
    mp = os.path.join(root, MEDIA_FILE)
    cur = load_json(mp, {'schema': SCHEMA, 'titleId': title, 'cover': '', 'shots': []})
    cur['titleId'] = title
    if a.kind == 'cover':
        cur['cover'] = 'cover.jpg'
    cur.setdefault('shots', []).append({
        'file': rel, 'kind': a.kind, 'scene': a.scene, 'titleId': title,
        'at': iso_now(), 'width': 0, 'height': 0,
    })
    cur['shots'] = cur['shots'][-200:]
    save_json(mp, cur)
    print('perf_record: %s -> %s' % (rel, 'cover.jpg' if a.kind == 'cover' else mp))
    return 0


def cmd_show(a):
    root = os.path.abspath(a.root)
    title, version = pack_title(root)
    perf = load_json(os.path.join(root, PERF_FILE), None)
    media = load_json(os.path.join(root, MEDIA_FILE), None)
    print('title  %s  version %s' % (title, version or '-'))
    if not perf or not perf.get('records'):
        print('perf   none (the card shows 暂无性能数据)')
    else:
        for r in perf['records'][-5:]:
            print('perf   %-6s %-8s %6.1f ms %4d fps  %-12s %s' % (
                r.get('source', '?'), r.get('scene', '?'), r.get('frameMsP50', 0),
                r.get('fpsP50', 0), r.get('device', {}).get('model', '?'), r.get('capturedAt', '')))
    if not media or (not media.get('cover') and not media.get('shots')):
        print('media  none (the card draws its generated placeholder)')
    else:
        print('media  cover %s, %d shot(s)' % (media.get('cover') or '-', len(media.get('shots') or [])))
    return 0


def main():
    ap = argparse.ArgumentParser(description='Record a measurement or a picture where the library reads them.')
    sub = ap.add_subparsers(dest='cmd', required=True)
    m = sub.add_parser('from-measure', help='append a record from measure.py --json')
    m.add_argument('--measure', required=True)
    m.add_argument('--root', required=True, help='the pack dir (holds nx2a-data.json)')
    m.add_argument('--scene', default='field')
    m.add_argument('--model', default='', help='device model when --hdc cannot be reached')
    m.add_argument('--hdc')
    m.add_argument('--target', default='')
    s = sub.add_parser('shot', help='capture the panel into shots/ (and cover.jpg)')
    s.add_argument('--root', required=True)
    s.add_argument('--scene', default='field')
    s.add_argument('--kind', default='scene', choices=['scene', 'cover'])
    s.add_argument('--hdc')
    s.add_argument('--target', default='')
    sh = sub.add_parser('show', help='print what the library will read')
    sh.add_argument('--root', required=True)
    a = ap.parse_args()
    if a.cmd == 'from-measure':
        return cmd_from_measure(a)
    if a.cmd == 'shot':
        if not a.target:
            sys.exit('perf_record: shot needs --target (hdc list targets -v)')
        return cmd_shot(a)
    return cmd_show(a)


if __name__ == '__main__':
    sys.exit(main())
