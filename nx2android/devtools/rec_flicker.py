# Record the device's screen for a few seconds and find flicker: frames that differ from both neighbours while the neighbours agree.   rec_flicker.py <out dir> [seconds]
# Writes rec.mp4 and, for the worst frames, PNGs of the frame and its predecessor.
import os, subprocess, sys
import av, numpy as np
from PIL import Image
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from device import ADB   # noqa: E402  (devtools/device.json: the device)
out = sys.argv[1]; secs = int(sys.argv[2]) if len(sys.argv) > 2 else 4
os.makedirs(out, exist_ok=True); env = dict(os.environ, MSYS_NO_PATHCONV='1'); mp4 = os.path.join(out, 'rec.mp4')
subprocess.run(ADB + ['shell', f'screenrecord --size 1520x952 --bit-rate 40000000 --time-limit {secs} /data/local/tmp/rec.mp4'], env=env, timeout=secs + 60, check=True)
subprocess.run(ADB + ['pull', '/data/local/tmp/rec.mp4', mp4], env=env, timeout=120, check=True, capture_output=True)
fr, ts = [], []
with av.open(mp4) as c:
    for f in c.decode(video=0): fr.append(f.to_ndarray(format='rgb24')[::2, ::2].astype(np.int16)); ts.append(float(f.time or 0))
print(f'{len(fr)} frames over {ts[-1] - ts[0]:.2f} s' if fr else 'no frames')
blk = 16   # per-block mean differences, so one small object flickering stands out from noise
def blocks(a): h, w = a.shape[0] // blk * blk, a.shape[1] // blk * blk; return a[:h, :w].reshape(h // blk, blk, w // blk, blk, 3).mean((1, 3))
B = [blocks(f) for f in fr]; res = []
for i in range(1, len(B) - 1):
    dp = np.abs(B[i] - B[i - 1]).mean(2); dn = np.abs(B[i] - B[i + 1]).mean(2); dnb = np.abs(B[i - 1] - B[i + 1]).mean(2)
    spike = np.minimum(dp, dn) - dnb   # high where frame i is unlike both neighbours that agree with each other
    res.append((float(spike.max()), int((spike > 25).sum()), i, np.unravel_index(spike.argmax(), spike.shape)))
res.sort(reverse=True)
for s, n, i, (by, bx) in res[:8]:
    print(f'frame {i} t={ts[i]:.3f}: worst block spike {s:5.1f} at ({bx * blk * 4}, {by * blk * 4}) screen px, blocks over 25: {n}')
for s, n, i, _ in res[:3]:
    if s > 25: Image.fromarray(np.concatenate([fr[i - 1], fr[i]], 1).astype(np.uint8)).save(os.path.join(out, f'spike_{i:04d}.png'))
print(f'frames with a spike over 25: {sum(1 for r in res if r[0] > 25)}/{len(res)}')
