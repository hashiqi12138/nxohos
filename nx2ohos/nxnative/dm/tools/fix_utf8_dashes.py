"""Repair the mojibake PowerShell left in a UTF-8 source file.

A PowerShell `-replace` + `Set-Content -NoNewline` round-trip on this host writes the file through the console's
cp936 page, which turns U+2014 (em dash, E2 80 94) and U+2013 (en dash, E2 80 93) into a truncated E2 80 3F
("?"). This walks the bytes, rewrites every such sequence as ASCII "--", verifies the result decodes as UTF-8,
and reports what it changed.

    python tools/fix_utf8_dashes.py <file> [...]
"""
import sys


def fix(path):
    with open(path, 'rb') as f:
        b = f.read()
    before = b
    n = 0
    out = bytearray()
    i = 0
    while i < len(b):
        # A UTF-8 multi-byte sequence whose tail became "?" is the console-page round-trip's damage: the lead
        # bytes survive, the final byte was rewritten as 0x3F. Repair by ASCII stand-ins for the common ones.
        if i + 2 < len(b) and b[i] in (0xE2, 0xE3) and b[i + 1] >= 0x80 and b[i + 2] == 0x3F:
            pair = (b[i], b[i + 1])
            if pair == (0xE2, 0x80):   # U+2013/2014 dashes
                out += b'--'
            elif pair == (0xE2, 0x86):  # U+2192 right arrow
                out += b'->'
            elif pair == (0xE2, 0x89):  # U+2264/2265 comparisons
                out += b'<='
            elif pair == (0xE2, 0x9C):  # U+2705 check
                out += b'OK'
            else:
                out += b'?'
            i += 3
            n += 1
            continue
        out.append(b[i])
        i += 1
    b = bytes(out)
    # any other non-ASCII byte left is a real character (a CJK comment) or another break: report it
    try:
        b.decode('utf-8')
        ok = True
    except UnicodeDecodeError as e:
        ok = False
        print('%s: STILL INVALID UTF-8 at %d: %s' % (path, e.start, b[max(0, e.start - 20):e.start + 20]))
    if b != before:
        with open(path, 'wb') as f:
            f.write(b)
    print('%s: %d dash sequence(s) fixed, valid UTF-8: %s, %d -> %d bytes'
          % (path, n, ok, len(before), len(b)))
    return ok


if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit('usage: fix_utf8_dashes.py <file> [...]')
    good = all(fix(p) for p in sys.argv[1:])
    sys.exit(0 if good else 1)
