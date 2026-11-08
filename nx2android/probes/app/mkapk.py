#!/usr/bin/env python3
# mkapk.py: build and sign a code-less NativeActivity APK with the Python standard library only (no JDK, no Android SDK).
#   binary AndroidManifest.xml (AXML) -> stored ZIP with the .so page-aligned -> APK Signature Scheme v2 (RSA-2048, SHA-256) with a self-signed cert.
# Usage: mkapk.py <libprobe.so> <out.apk> [key.json]   (the key is created on first use and reused, so reinstalls keep the same signer)
import hashlib, json, os, secrets, struct, sys, time, zlib

PKG, LIB, LABEL = 'org.nx2a.probe', 'probe', 'NX2A Probe'
MIN_SDK, TARGET_SDK = 29, 36
ANDROID_NS = 'http://schemas.android.com/apk/res/android'

# ---------------- binary XML ----------------
ATTR_IDS = {  # android: attribute resource IDs (frameworks/base/core/res/res/values/public.xml)
    'label': 0x01010001, 'name': 0x01010003, 'hasCode': 0x0101000c, 'debuggable': 0x0101000f, 'exported': 0x01010010,
    'configChanges': 0x0101001f, 'value': 0x01010024, 'minSdkVersion': 0x0101020c, 'versionCode': 0x0101021b,
    'versionName': 0x0101021c, 'targetSdkVersion': 0x01010270, 'extractNativeLibs': 0x010104ea}
T_STRING, T_INT_DEC, T_INT_HEX, T_BOOL = 0x03, 0x10, 0x11, 0x12

# (tag, [(ns_android?, name, value)], children)
MANIFEST = ('manifest', [(False, 'package', PKG), (True, 'versionCode', 1), (True, 'versionName', '1.0')], [
    ('uses-sdk', [(True, 'minSdkVersion', MIN_SDK), (True, 'targetSdkVersion', TARGET_SDK)], []),
    ('application', [(True, 'label', LABEL), (True, 'hasCode', False), (True, 'debuggable', True), (True, 'extractNativeLibs', True)], [
        ('activity', [(True, 'name', 'android.app.NativeActivity'), (True, 'exported', True), (True, 'configChanges', ('hex', 0x4a0))], [
            ('meta-data', [(True, 'name', 'android.app.lib_name'), (True, 'value', LIB)], []),
            ('intent-filter', [], [
                ('action', [(True, 'name', 'android.intent.action.MAIN')], []),
                ('category', [(True, 'name', 'android.intent.category.LAUNCHER')], [])])])])])

def axml(root):
    # string pool: attribute names that carry resource IDs come first, in resource-map order
    res_names = sorted({n for _, n, _ in walk_attrs(root) if n in ATTR_IDS}, key=lambda n: ATTR_IDS[n])
    strings = list(res_names)
    def s(x):
        if x not in strings: strings.append(x)
        return strings.index(x)
    s('android'); s(ANDROID_NS)
    body = []
    def chunk(t, hsize, payload): return struct.pack('<HHI', t, hsize, 8 + len(payload)) + payload
    def node(n, line=[1]):
        tag, attrs, kids = n
        enc = []
        for is_ns, name, v in attrs:
            ns = s(ANDROID_NS) if is_ns else 0xffffffff
            if isinstance(v, bool): raw, dt, data = 0xffffffff, T_BOOL, 0xffffffff if v else 0
            elif isinstance(v, int): raw, dt, data = 0xffffffff, T_INT_DEC, v
            elif isinstance(v, tuple): raw, dt, data = 0xffffffff, T_INT_HEX, v[1]
            else: raw, dt, data = s(v), T_STRING, s(v)
            enc.append((ATTR_IDS.get(name, 0xffffffff) if is_ns else 0xffffffff, struct.pack('<IIIHBBI', ns, s(name), raw, 8, 0, dt, data)))
        enc.sort(key=lambda a: a[0])   # resource-ID attributes ascending, plain ones (package) last
        a = b''.join(e for _, e in enc)
        body.append(chunk(0x0102, 16, struct.pack('<II', line[0], 0xffffffff) + struct.pack('<IIHHHHHH', 0xffffffff, s(tag), 20, 20, len(attrs), 0, 0, 0) + a))
        line[0] += 1
        for k in kids: node(k)
        body.append(chunk(0x0103, 16, struct.pack('<IIII', line[0], 0xffffffff, 0xffffffff, s(tag)))); line[0] += 1
    ns = struct.pack('<IIII', 1, 0xffffffff, s('android'), s(ANDROID_NS))
    node(root)
    nodes = chunk(0x0100, 16, ns) + b''.join(body) + chunk(0x0101, 16, ns)
    # UTF-16 string pool
    offs, data = [], b''
    for x in strings:
        offs.append(len(data)); u = x.encode('utf-16-le'); data += struct.pack('<H', len(x)) + u + b'\0\0'
    data += b'\0' * (-len(data) % 4)
    hdr = struct.pack('<IIIII', len(strings), 0, 0, 28 + 4 * len(strings), 0)
    pool = chunk(0x0001, 28, hdr + b''.join(struct.pack('<I', o) for o in offs) + data)
    rmap = chunk(0x0180, 8, b''.join(struct.pack('<I', ATTR_IDS[n]) for n in res_names))
    return chunk(0x0003, 8, pool + rmap + nodes)

def walk_attrs(n):
    for a in n[1]: yield a
    for k in n[2]: yield from walk_attrs(k)

# ---------------- RSA, DER, self-signed certificate ----------------
def is_prime(n):
    if n < 2: return False
    for p in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        if n % p == 0: return n == p
    d, r = n - 1, 0
    while d % 2 == 0: d //= 2; r += 1
    for _ in range(40):
        x = pow(secrets.randbelow(n - 3) + 2, d, n)
        if x in (1, n - 1): continue
        for _ in range(r - 1):
            x = pow(x, 2, n)
            if x == n - 1: break
        else: return False
    return True
def gen_prime(bits):
    while True:
        p = secrets.randbits(bits) | (3 << (bits - 2)) | 1
        if p % 65537 != 1 and is_prime(p): return p
def gen_key():
    p, q = gen_prime(1024), gen_prime(1024)
    n, e = p * q, 65537
    return {'n': n, 'e': e, 'd': pow(e, -1, (p - 1) * (q - 1))}

def der(tag, body):
    l = len(body)
    ln = bytes([l]) if l < 0x80 else (lambda b: bytes([0x80 | len(b)]) + b)(l.to_bytes((l.bit_length() + 7) // 8, 'big'))
    return bytes([tag]) + ln + body
def der_int(v): return der(0x02, v.to_bytes(v.bit_length() // 8 + 1, 'big'))
def der_seq(*xs): return der(0x30, b''.join(xs))
def der_oid(s):
    a = [int(x) for x in s.split('.')]; out = bytes([a[0] * 40 + a[1]])
    for v in a[2:]:
        b = [v & 0x7f]; v >>= 7
        while v: b.append(0x80 | (v & 0x7f)); v >>= 7
        out += bytes(reversed(b))
    return der(0x06, out)
NULL = b'\x05\x00'
ALG_SHA256_RSA = der_seq(der_oid('1.2.840.113549.1.1.11'), NULL)
def spki(k): return der_seq(der_seq(der_oid('1.2.840.113549.1.1.1'), NULL), der(0x03, b'\0' + der_seq(der_int(k['n']), der_int(k['e']))))
def rsa_sign(k, msg):
    di = bytes.fromhex('3031300d060960864801650304020105000420') + hashlib.sha256(msg).digest()
    kl = (k['n'].bit_length() + 7) // 8
    em = b'\x00\x01' + b'\xff' * (kl - len(di) - 3) + b'\x00' + di
    return pow(int.from_bytes(em, 'big'), k['d'], k['n']).to_bytes(kl, 'big')
def cert(k):
    name = der_seq(der(0x31, der_seq(der_oid('2.5.4.3'), der(0x0c, b'nx2a probe'))))
    validity = der_seq(der(0x17, b'260101000000Z'), der(0x18, b'20560101000000Z'))
    tbs = der_seq(der(0xa0, der_int(2)), der_int(secrets.randbits(62) | 1), ALG_SHA256_RSA, name, validity, name, spki(k))
    return der_seq(tbs, ALG_SHA256_RSA, der(0x03, b'\0' + rsa_sign(k, tbs)))

# ---------------- ZIP (stored) ----------------
def build_zip(entries):   # entries: [(name, data, align)]
    out, cd = bytearray(), bytearray()
    dos_time, dos_date = 0, (2026 - 1980) << 9 | 1 << 5 | 1
    for name, data, align in entries:
        nb = name.encode(); crc = zlib.crc32(data)
        pad = -(len(out) + 30 + len(nb)) % align
        if 0 < pad < 4: pad += align   # too small for an extra field header: go to the next alignment step
        extra = struct.pack('<HH', 0xd935, pad - 4) + b'\0' * (pad - 4) if pad else b''   # 0xd935: zipalign's alignment extra field
        off = len(out)
        out += struct.pack('<IHHHHHIIIHH', 0x04034b50, 10, 0, 0, dos_time, dos_date, crc, len(data), len(data), len(nb), len(extra)) + nb + extra + data
        cd += struct.pack('<IHHHHHHIIIHHHHHII', 0x02014b50, 10, 10, 0, 0, dos_time, dos_date, crc, len(data), len(data), len(nb), 0, 0, 0, 0, 0, off) + nb
    eocd = struct.pack('<IHHHHIIH', 0x06054b50, 0, 0, len(entries), len(entries), len(cd), len(out), 0)
    return bytes(out), bytes(cd), eocd

# ---------------- APK Signature Scheme v2 ----------------
def lp(b): return struct.pack('<I', len(b)) + b
def chunked_sha256(sections):
    digests = []
    for sec in sections:
        for i in range(0, len(sec), 1 << 20):
            c = sec[i:i + (1 << 20)]
            digests.append(hashlib.sha256(b'\xa5' + struct.pack('<I', len(c)) + c).digest())
    return hashlib.sha256(b'\x5a' + struct.pack('<I', len(digests)) + b''.join(digests)).digest()
def sign_v2(zip_entries, cd, eocd, k, crt):
    ALG = 0x0103   # RSASSA-PKCS1-v1_5 with SHA2-256
    digest = chunked_sha256([zip_entries, cd, eocd])   # EOCD's CD offset still points at the CD start = where the signing block will begin
    signed = lp(lp(struct.pack('<I', ALG) + lp(digest))) + lp(lp(crt)) + lp(b'')
    signer = lp(signed) + lp(lp(struct.pack('<I', ALG) + lp(rsa_sign(k, signed)))) + lp(spki(k))
    value = lp(lp(signer))
    pair = struct.pack('<QI', 4 + len(value), 0x7109871a) + value
    size = len(pair) + 8 + 16
    block = struct.pack('<Q', size) + pair + struct.pack('<Q', size) + b'APK Sig Block 42'
    new_eocd = eocd[:16] + struct.pack('<I', len(zip_entries) + len(block)) + eocd[20:]
    return zip_entries + block + cd + new_eocd

def main():
    so, out = sys.argv[1], sys.argv[2]
    keyf = sys.argv[3] if len(sys.argv) > 3 else os.path.join(os.path.dirname(os.path.abspath(out)), 'probe-signing-key.json')
    if os.path.exists(keyf): k = {a: int(v) for a, v in json.load(open(keyf)).items() if a != 'cert'}; crt = bytes.fromhex(json.load(open(keyf))['cert'])
    else:
        t = time.time(); k = gen_key(); crt = cert(k)
        json.dump({**{a: str(v) for a, v in k.items()}, 'cert': crt.hex()}, open(keyf, 'w'))
        print(f'new signing key {keyf} ({time.time() - t:.1f} s)')
    entries = [('AndroidManifest.xml', axml(MANIFEST), 4), (f'lib/arm64-v8a/lib{LIB}.so', open(so, 'rb').read(), 16384)]
    z, cd, eocd = build_zip(entries)
    apk = sign_v2(z, cd, eocd, k, crt)
    open(out, 'wb').write(apk)
    print(f'{out}: {len(apk)} bytes, signer SHA-256 {hashlib.sha256(crt).hexdigest()[:16]}...')

if __name__ == '__main__': main()
