#!/usr/bin/env python3
"""Run the OHOS port's own unit tests and enforce the coverage gate.

    python dm/ohos/tests/run_tests.py              # run, report, fail under 90%
    python dm/ohos/tests/run_tests.py --no-gate    # run and report, do not fail on the number
    python dm/ohos/tests/run_tests.py --html       # also write dm/ohos/tests/cov/htmlcov/

This is what CI calls (`.github/workflows/ohos-unit-tests.yml`) and what a developer runs before pushing. The
tests live in dm/ohos/tests and cover the files this port added or rewrote; the upstream tooling keeps its own
suite in dm/tools/tests, which needs macOS and a game dump and so is not part of this gate.

Coverage matches by PATH, not by module name: conftest.load imports each file under a synthetic name so a script
that is not on sys.path can still be tested, and coverage's module-name matching cannot see through that.
"""
import argparse
import os
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[3]
TESTS = REPO / 'dm' / 'ohos' / 'tests'
COV_DIR = TESTS / 'cov'

# The files this port owns. Kept here as well as in .coveragerc so a file added later is a one-line change and the
# include pattern cannot silently widen to all of dm/.
OWNED = [
    'dm/tools/pack_game.py',
    'dm/tools/pack_server.py',
    'dm/tools/gen_pack_manifest.py',
    'dm/tools/fix_utf8_dashes.py',
    'dm/tools/daily_sync.py',
    'dm/tools/export_ohos_patches.py',
    'dm/tools/test_ohos_patches.py',
    'dm/ohos/build_ohos.py',
    'dm/ohos/devpush.py',
    'dm/ohos/e2e_smoke.py',
    'dm/ohos/mkhap.py',
]

# Lines no unit test can reach are marked in the source with `# pragma: no cover` and the reason, which is
# coverage's own mechanism: an exclusion stays visible at the line it applies to instead of living in a list here.


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--no-gate', action='store_true', help='report coverage but do not fail below 90%')
    ap.add_argument('--html', action='store_true', help='also write an HTML report')
    ap.add_argument('-k', help='pytest -k expression, for one file while iterating')
    a = ap.parse_args()

    missing = [p for p in OWNED if not (REPO / p).is_file()]
    if missing:
        print('run_tests: these owned files are gone (update OWNED):')
        for m in missing:
            print('  ' + m)
        return 2

    COV_DIR.mkdir(parents=True, exist_ok=True)
    include = ','.join(OWNED)

    env = dict(os.environ)
    env['PYTHONUTF8'] = '1'

    # --include matches by PATH, which is what a by-path import needs. .coveragerc deliberately sets no
    # `source`: that matches by module name and reports "was never imported" for every file conftest loads under
    # a synthetic name.
    cmd = [sys.executable, '-m', 'coverage', 'run',
           '--include=' + include,
           '--data-file=' + str(COV_DIR / '.coverage'),
           '-m', 'pytest', str(TESTS), '-q', '--no-header', '-p', 'no:cacheprovider']
    if a.k:
        cmd += ['-k', a.k]
    print('+ ' + ' '.join(cmd))
    rc = subprocess.run(cmd, cwd=str(REPO), env=env).returncode
    if rc != 0:
        print('\nrun_tests: the tests failed (pytest exit %d); coverage is not reported' % rc)
        return rc

    print()
    rep = [sys.executable, '-m', 'coverage', 'report',
           '--data-file=' + str(COV_DIR / '.coverage')]
    if not a.no_gate:
        rep += ['--fail-under=90']
    rc_rep = subprocess.run(rep, cwd=str(REPO), env=env).returncode

    if a.html:
        subprocess.run([sys.executable, '-m', 'coverage', 'html',
                        '--data-file=' + str(COV_DIR / '.coverage'),
                        '-d', str(COV_DIR / 'htmlcov')], cwd=str(REPO), env=env)

    # A Markdown table for the CI job summary, so a reviewer sees the number without opening the log.
    summary = COV_DIR / 'summary.md'
    with open(summary, 'w', encoding='utf-8') as f:
        f.write('### OHOS unit tests\n\n')
        f.write('```\n')
        subprocess.run([sys.executable, '-m', 'coverage', 'report',
                        '--data-file=' + str(COV_DIR / '.coverage'),
                        '--format=total'], cwd=str(REPO), env=env, stdout=f)
        f.write('```\n')
    print('\nrun_tests: coverage table -> %s' % summary)
    return rc_rep


if __name__ == '__main__':
    sys.exit(main())
