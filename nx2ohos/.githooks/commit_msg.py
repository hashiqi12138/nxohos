# Conventional Commits 1.0.0 (https://www.conventionalcommits.org/en/v1.0.0/) for this repository's commit messages, with
# @commitlint/config-conventional's types and line limits. .githooks/commit-msg runs it; `git config core.hooksPath .githooks` turns it on.
#   commit_msg.py <message file>    exit 1 and the broken rules on stderr when the message is refused
#
#   <type>[(<scope>)][!]: <description>
#
#   [body: free text, one blank line after the header]
#
#   [footers: Token: value / Token #value, BREAKING CHANGE: <what breaks>]
#
# The description may start with a capital: subjects here open with game and API names (Xenoblade Chronicles 3, Vulkan, NEO).
# Messages git writes itself (merges, reverts, fixup!/squash!/amend!) are let through.
import re, subprocess, sys

TYPES = ('build', 'chore', 'ci', 'docs', 'feat', 'fix', 'perf', 'refactor', 'revert', 'style', 'test')
MAX_LINE = 100
HEADER_RX = re.compile(r'(?P<type>[^\s(!:]+)(?:\((?P<scope>[^()]*)\))?(?P<bang>!)?: (?P<desc>.*)')
GIT_MADE_RX = re.compile(r'Merge |Revert "|Reapply "|(?:fixup|squash|amend)! ')
SCISSORS = ' ------------------------ >8 ------------------------'


def comment_char():
    try: c = subprocess.run(['git', 'config', '--get', 'core.commentChar'], capture_output=True, text=True).stdout.strip()
    except OSError: c = ''
    return c if c and c != 'auto' else '#'


def clean(text, cc='#'):
    """The message as git will store it: no comment lines, nothing from the scissors line on (git commit -v), no trailing blanks"""
    lines = []
    for ln in text.splitlines():
        if ln == cc + SCISSORS: break
        if not ln.startswith(cc): lines.append(ln.rstrip())
    while lines and not lines[-1]: lines.pop()
    while lines and not lines[0]: lines.pop(0)
    return lines


def check(lines):
    """The rules this message breaks, as sentences; empty when it passes"""
    if not lines or GIT_MADE_RX.match(lines[0]): return []
    header, errs = lines[0], []
    m = HEADER_RX.fullmatch(header)
    if not m:
        return [f'the header must be "<type>[(<scope>)][!]: <description>", with one of: {", ".join(TYPES)}']
    t, scope, desc = m['type'], m['scope'], m['desc']
    if t not in TYPES:
        errs.append(f'"{t}" is not a type: use ' + (f'"{t.lower()}"' if t.lower() in TYPES else 'one of ' + ', '.join(TYPES)))
    if scope is not None and (not scope.strip() or scope != scope.strip()):
        errs.append('the scope in ( ) is empty or has spaces around it')
    if not desc.strip(): errs.append('the description after ": " is empty')
    elif desc != desc.strip(): errs.append('the description has extra spaces around it')
    elif desc.endswith('.'): errs.append('the description ends with "."')
    if len(header) > MAX_LINE: errs.append(f'the header is {len(header)} characters, at most {MAX_LINE}')
    if len(lines) > 1 and lines[1]: errs.append('the body must start after one blank line under the header')
    for i, ln in enumerate(lines[1:], 2):
        if len(ln) > MAX_LINE and re.search(r'\s', ln.strip()):   # a lone URL or path may run long
            errs.append(f'line {i} is {len(ln)} characters, at most {MAX_LINE}')
    for ln in lines[1:]:
        if re.match(r'breaking[ -]change:', ln, re.I) and not re.match(r'BREAKING[ -]CHANGE: \S', ln):
            errs.append('the breaking-change footer is "BREAKING CHANGE: <what breaks>", in capitals')
    return errs


def main(argv):
    if len(argv) != 2: sys.exit('usage: commit_msg.py <message file>')
    with open(argv[1], encoding='utf-8', errors='replace') as f: lines = clean(f.read(), comment_char())
    errs = check(lines)
    if not errs: return 0
    print('commit-msg: the message does not follow Conventional Commits (https://www.conventionalcommits.org/en/v1.0.0/)', file=sys.stderr)
    print(f'  header: {lines[0]}', file=sys.stderr)
    for e in errs: print(f'  - {e}', file=sys.stderr)
    print('  e.g.   fix(vk): a transfer read where a run of rasterizer-discard draws ends', file=sys.stderr)
    print('         feat(profile): Hades II playable on update 1.0 (v655360)', file=sys.stderr)
    print('  Edit it and retry:  git commit --edit --file=.git/COMMIT_EDITMSG   (skip the check: --no-verify)', file=sys.stderr)
    return 1


if __name__ == '__main__':
    sys.exit(main(sys.argv))
