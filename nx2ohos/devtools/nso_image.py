# nso_image.py <NSO> <out.orig.bin>: the module's image as it is laid out in memory (text, ro, data at their offsets; lz4 segments decompressed),
# the "original image" dm/tools/sdis.py disassembles.
import struct, sys
import lz4.block
d = open(sys.argv[1], 'rb').read()
assert d[:4] == b'NSO0'
flags = struct.unpack_from('<I', d, 0xc)[0]
segs = [struct.unpack_from('<III', d, 0x10 + i * 0x10) for i in range(3)]
csz = struct.unpack_from('<III', d, 0x60)
img = bytearray(segs[2][1] + segs[2][2])
for i, (fo, mo, sz) in enumerate(segs):
    raw = d[fo:fo + csz[i]]
    if flags >> i & 1: raw = lz4.block.decompress(raw, uncompressed_size=sz)
    img[mo:mo + sz] = raw
open(sys.argv[2], 'wb').write(img)
print(f'{sys.argv[2]}: {len(img):#x} bytes')
