"""Generate dm.files.tsv for a porter data pack (the dev import's manifest: a stamp over rel/size/mtime).

    python tools/gen_pack_manifest.py --pack <dir> [--out <dm.files.tsv>]

The same shape mkhap.py writes: a "# DM_DATA <sha1>" header, then one "rel<TAB>size<TAB>module" line per file.
The dev import serves every file from the pack, so the module column is always "entry".
"""
import argparse, hashlib, os, sys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pack', required=True)
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    pack = os.path.abspath(a.pack)
    if not os.path.isdir(pack): sys.exit('gen_pack_manifest: no such pack: %s' % pack)
    files = sorted(os.path.relpath(os.path.join(d, f), pack).replace('\\', '/')
                   for d, _, fs_ in os.walk(pack) for f in fs_ if f != 'dm.files.tsv')
    h = hashlib.sha1()
    for rel in files:
        st = os.stat(os.path.join(pack, rel))
        h.update(('%s\t%d\t%d\n' % (rel, st.st_size, st.st_mtime_ns)).encode())
    stamp = h.hexdigest()
    with open(a.out, 'w', encoding='utf-8', newline='\n') as f:
        f.write('# DM_DATA %s\n' % stamp)
        for rel in files:
            f.write('%s\t%d\tentry\n' % (rel, os.path.getsize(os.path.join(pack, rel))))
    print('gen_pack_manifest: %d files, stamp %s -> %s' % (len(files), stamp[:12], a.out))


if __name__ == '__main__':
    main()
