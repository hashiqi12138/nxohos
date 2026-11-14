#!/usr/bin/env python3
# darwin2elf.py <in.S> <out.S>: an AArch64 assembly file written for Mach-O (the host's entry.S, nvn_entry.S, the generated nvn_thunks.S) as ELF assembly
# for the Android build. C symbols lose Mach-O's leading underscore, adrp / add / ldr operands sym@PAGE / sym@PAGEOFF become sym / :lo12:sym,
# .private_extern becomes .hidden, and Mach-O's section shorthands become ELF sections. Local labels (1:, 1f) and numbers are untouched.
# A symbol is "C" when it starts with one underscore followed by a letter.
import re, sys

src, dst = sys.argv[1], sys.argv[2]
SYM = r'_([A-Za-z][A-Za-z0-9_$.]*)'
SECTIONS = [(r'\.cstring\b', '.section .rodata.str1.1,"aMS",@progbits,1'), (r'\.const_data\b', '.section .data.rel.ro'), (r'\.const\b', '.section .rodata')]
out = []
def zerofill(m):
    ind, sym, size, align = m.group(1), m.group(2), m.group(3), m.group(4) or '0'
    return f'{ind}.bss\n{ind}.p2align {align}\n{ind}{sym}:\n{ind}.zero {size}\n{ind}.text'
for line in open(src, encoding='utf-8'):
    code, sep, comment = line.partition('//')
    code = re.sub(r'^(\s*)\.zerofill\s+[^,]+,[^,]+,' + SYM + r',(\d+)(?:,(\d+))?', zerofill, code)   # Mach-O zero-filled BSS → ELF .bss + .zero
    code = re.sub(SYM + r'@PAGEOFF', r':lo12:\1', code)
    code = re.sub(SYM + r'@PAGE', r'\1', code)
    code = code.replace('.private_extern', '.hidden')
    # the C build is -fvisibility=hidden; match it for assembly globals so they are non-preemptible (adrp PC-relative is then allowed in the shared lib)
    code = re.sub(r'^(\s*)\.globl\s+' + SYM + r'[ \t]*$', lambda m: f'{m.group(1)}.globl {m.group(2)}\n{m.group(1)}.hidden {m.group(2)}', code)
    for pat, rep in SECTIONS: code = re.sub(r'^(\s*)' + pat, lambda m: m.group(1) + rep, code)
    code = re.sub(r'(\.globl\s+|\.hidden\s+|\b(?:bl|b|b\.\w+|cbz\s+\w+,|cbnz\s+\w+,|tbz\s+\w+,\s*#\d+,|tbnz\s+\w+,\s*#\d+,|adr\s+\w+,)\s*)' + SYM + r'\b', r'\1\2', code)
    code = re.sub(r'^(\s*)' + SYM + r':', r'\1\2:', code)   # label definitions
    code = re.sub(r'(\.(?:quad|word|xword|8byte)\s+)' + SYM + r'\b', r'\1\2', code)   # data references
    out.append(code + sep + comment)
text = ''.join(out)
if '@PAGE' in text: sys.exit(f'{src}: unconverted @PAGE operand')
open(dst, 'w', encoding='utf-8', newline='\n').write(text)
