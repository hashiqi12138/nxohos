"""count_matching_frames.py SOURCE R0 R1 G0 G1 B0 B1 [THRESHOLD]

Which frames show a known glitch colour: a frame counts when more than THRESHOLD (default 0.04) of its pixels fall inside the RGB box
[R0..R1] x [G0..G1] x [B0..B1]. SOURCE is a folder of frame_*.ppm / *.png (frame_shots.sh) or an .mp4 (devtools/rec_flicker.py's rec.mp4).
NEO's brown wipe squares were 35 110 20 75 0 35: 2 of 8 shot frames before the fix, 0 after.
Run with plain `python`, not `python -I`: it needs Pillow and numpy (and PyAV for .mp4) from user site-packages.
"""
import glob, os, sys
import numpy as np


def frames(src):
    if os.path.isdir(src):
        from PIL import Image
        for p in sorted(glob.glob(os.path.join(src, 'frame_*.ppm')) + glob.glob(os.path.join(src, '*.png'))):
            yield os.path.basename(p), np.asarray(Image.open(p).convert('RGB'))
    else:
        import av
        with av.open(src) as c:
            for i, f in enumerate(c.decode(video=0)):
                yield f'frame {i} t={float(f.time or 0):.3f}', f.to_ndarray(format='rgb24')


def main():
    if len(sys.argv) < 8: sys.exit(__doc__)
    src = sys.argv[1]; r0, r1, g0, g1, b0, b1 = (int(x) for x in sys.argv[2:8]); thr = float(sys.argv[8]) if len(sys.argv) > 8 else 0.04
    n = hit = 0
    for name, a in frames(src):
        a = a[::4, ::4].astype(int); r, g, b = a[..., 0], a[..., 1], a[..., 2]
        frac = ((r >= r0) & (r <= r1) & (g >= g0) & (g <= g1) & (b >= b0) & (b <= b1)).mean()
        n += 1
        if frac > thr: hit += 1; print(f'{name}: {frac * 100:.1f}% in the box')
    print(f'{hit} of {n} frames over {thr * 100:.0f}%')


if __name__ == '__main__':
    main()
