# PPM (P6, as the Vulkan backend's DM_VK_SHOT writes) → PNG at half size, stdlib only.   ppm2png.py <in.ppm>... (writes <in>.png beside each)
import struct, sys, zlib
for src in sys.argv[1:]:
    b = open(src, 'rb').read(); parts = b.split(b'\n', 3); w, h = map(int, parts[1].split()); px = parts[3]
    W, H = w // 2, h // 2
    raw = b''.join(b'\0' + b''.join(px[(2 * y) * w * 3 + 6 * x:(2 * y) * w * 3 + 6 * x + 3] for x in range(W)) for y in range(H))
    ch = lambda t, d: struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)
    open(src[:-4] + '.png', 'wb').write(b'\x89PNG\r\n\x1a\n' + ch(b'IHDR', struct.pack('>IIBBBBB', W, H, 8, 2, 0, 0, 0)) + ch(b'IDAT', zlib.compress(raw, 6)) + ch(b'IEND', b''))
    print(src[:-4] + '.png')
