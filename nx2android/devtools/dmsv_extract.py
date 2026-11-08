# Extract shaders from a DMSV bundle (dm/android/tools/shader_vk.py) by key, as <key>.spv beside the output prefix.   dmsv_extract.py <bundle> <out dir> <key hex>...
import os, struct, sys
b = open(sys.argv[1], 'rb'); hdr = b.read(12); assert hdr[:4] == b'DMSV'; n = struct.unpack_from('<I', hdr, 8)[0]
ents = [struct.unpack('<QIIIIII', b.read(32)) for _ in range(n)]
want = {int(k, 16) for k in sys.argv[3:]}
for key, st, so, sl, ro, rl, _ in ents:
    if key in want:
        b.seek(so); spv = b.read(sl); p = os.path.join(sys.argv[2], f'{key:016x}.spv'); open(p, 'wb').write(spv)
        print(f'{key:016x} stage {st}: {sl} bytes SPIR-V, reflection {rl} bytes -> {p}')
