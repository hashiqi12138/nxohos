# Screenshot burst from the device and per-shot stats of one screen region, to count frames that render wrong.   burst.py <out dir> [count] [x0 y0 x1 y1 as fractions]
# Prints each shot's mean colour of the region and its difference from the burst's median shot; outliers are the bad frames.
import os, subprocess, sys
import numpy as np
from PIL import Image
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from device import ADB   # noqa: E402  (devtools/device.json: the device)
out = sys.argv[1]; n = int(sys.argv[2]) if len(sys.argv) > 2 else 12
reg = [float(v) for v in sys.argv[3:7]] if len(sys.argv) > 6 else [.5, .55, .95, .95]
os.makedirs(out, exist_ok=True)
env = dict(os.environ, MSYS_NO_PATHCONV='1')
subprocess.run(ADB + ['shell', ' '.join(f'screencap -p /data/local/tmp/b{i}.png;' for i in range(n))], env=env, timeout=300, check=True)
rs = []
for i in range(n):
    p = os.path.join(out, f'b{i}.png')
    subprocess.run(ADB + ['pull', f'/data/local/tmp/b{i}.png', p], env=env, timeout=60, check=True, capture_output=True)
    a = np.asarray(Image.open(p).convert('RGB'), dtype=np.float32); h, w, _ = a.shape
    rs.append(a[int(h * reg[1]):int(h * reg[3]), int(w * reg[0]):int(w * reg[2])][::4, ::4])
med = np.median(np.stack(rs), axis=0)
bad = 0
for i, r in enumerate(rs):
    d = float(np.abs(r - med).mean()); m = r.reshape(-1, 3).mean(0); bad += d > 20
    print(f'b{i}: mean rgb {m[0]:6.1f} {m[1]:6.1f} {m[2]:6.1f}  diff vs median {d:6.1f}' + ('  <-- odd' if d > 20 else ''))
print(f'odd frames: {bad}/{n}')
