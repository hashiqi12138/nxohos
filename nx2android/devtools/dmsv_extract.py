# 按 shader key 抽取 DMSV；--reflection 同时保存运行时绑定表与内置 C1。
import argparse
import hashlib
import json
from pathlib import Path
import struct


KINDS = ('support', 'cbuf', 'c1', 'drvcb', 'sbuf', 'ubo_ptr', 'sbuf_ubo',
         'tex', 'tbuf', 'img', 'img_tbuf', 'tex_c2a', 'tex_ubo', 'tbuf_ubo', 'img_ubo')
DESCS = ('ubo', 'ssbo', 'sampled', 'storage', 'tbuf', 'storage_tbuf', 'image', 'sampler')


def reflection(spv, data):
    names, bindings, sets = {}, {}, {}
    words = struct.unpack(f'<{len(spv) // 4}I', spv)
    if len(words) < 5 or words[0] != 0x07230203:
        raise ValueError('无效 SPIR-V 文件头')
    i = 5
    while i < len(words):
        n, op = words[i] >> 16, words[i] & 0xffff
        if not n or i + n > len(words):
            raise ValueError('SPIR-V 指令截断')
        ins = words[i:i + n]
        if op == 5:
            names[ins[1]] = struct.pack(f'<{n - 2}I', *ins[2:]).split(b'\0')[0].decode()
        elif op == 71 and n == 4:
            if ins[2] == 33:
                bindings[ins[1]] = ins[3]
            elif ins[2] == 34:
                sets[ins[1]] = ins[3]
        i += n
    nb, nc, *io = struct.unpack_from('<5I', data)
    if len(data) != 20 + nb * 20 + nc:
        raise ValueError('DMSV 反射长度不匹配')
    rows = []
    fields = ('kind', 'descriptor', 'dim', 'flags', 'binding', 'count', 'reg',
              'sampler_source', 'image_format', 'offset', 'sampler_arg')
    for i in range(nb):
        row = dict(zip(fields, struct.unpack_from('<BBBBHHhBBii', data, 20 + i * 20)))
        row['kind'] = KINDS[row['kind']] if row['kind'] < len(KINDS) else row['kind']
        row['descriptor'] = DESCS[row['descriptor']] if row['descriptor'] < len(DESCS) else row['descriptor']
        row['variables'] = [{'id': v, 'name': names.get(v, ''), 'bundle_set': sets.get(v, 0)}
                            for v, b in bindings.items() if b == row['binding']]
        rows.append(row)
    c1 = data[20 + nb * 20:]
    return {'bindings': rows, 'io_or_local': io, 'c1_bytes': nc,
            'c1_words_hex': [f'{v:08x}' for (v,) in struct.iter_unpack('<I', c1)]}, c1


def main():
    ap = argparse.ArgumentParser(description='只读抽取指定 DMSV shader，不访问其他游戏资源')
    ap.add_argument('bundle', type=Path)
    ap.add_argument('out', type=Path)
    ap.add_argument('keys', nargs='+', type=lambda s: int(s, 16))
    ap.add_argument('--reflection', action='store_true', help='导出绑定 JSON 和内置 C1 二进制')
    a = ap.parse_args()
    if not a.out.is_dir():
        ap.error('输出目录必须已存在')
    wanted = set(a.keys)
    selected = []
    with a.bundle.open('rb') as f:
        size = a.bundle.stat().st_size
        hdr = f.read(12)
        if len(hdr) != 12:
            ap.error('DMSV 文件头截断')
        magic, version, count = struct.unpack('<4sII', hdr)
        if magic != b'DMSV' or version != 1 or 12 + count * 32 > size:
            ap.error('无效或不支持的 DMSV 文件头')
        for _ in range(count):
            entry = struct.unpack('<QIIIIII', f.read(32))
            if entry[0] in wanted:
                selected.append(entry)
        missing = wanted - {e[0] for e in selected}
        if missing:
            ap.error('未找到 shader: ' + ', '.join(f'{k:016x}' for k in sorted(missing)))
        for key, stage, so, sl, ro, rl, _ in selected:
            if min(so, ro) < 12 + count * 32 or so + sl > size or ro + rl > size or sl % 4:
                ap.error(f'{key:016x}: 数据范围错误')
            f.seek(so)
            spv = f.read(sl)
            f.seek(ro)
            data = f.read(rl)
            meta, c1 = reflection(spv, data)
            prefix = a.out / f'{key:016x}'
            prefix.with_suffix('.spv').write_bytes(spv)
            if a.reflection:
                meta.update(key=f'{key:016x}', stage=stage, bundle=str(a.bundle.resolve()),
                            spv_sha256=hashlib.sha256(spv).hexdigest(),
                            reflection_sha256=hashlib.sha256(data).hexdigest())
                prefix.with_suffix('.reflection.json').write_text(json.dumps(meta, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
                prefix.with_suffix('.c1.bin').write_bytes(c1)
            print(f'{key:016x} stage {stage}: SPIR-V {sl} 字节，反射 {rl} 字节，C1 {len(c1)} 字节')


if __name__ == '__main__':
    main()
