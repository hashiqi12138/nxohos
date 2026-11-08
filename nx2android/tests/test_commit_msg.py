# .githooks/commit_msg.py, the commit-msg hook's Conventional Commits rules (python -m unittest discover tests)
import importlib.util, subprocess, sys, tempfile, unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / '.githooks/commit_msg.py'
spec = importlib.util.spec_from_file_location('commit_msg', HOOK)
cm = importlib.util.module_from_spec(spec); spec.loader.exec_module(cm)


def errs(text): return cm.check(cm.clean(text))


class CommitMsg(unittest.TestCase):
    def test_accepted(self):
        for m in ('fix(vk): a transfer read where a run of rasterizer-discard draws ends (XC3 grass flicker)',
                  'feat(profile): Hades II playable on update 1.0 (v655360)',
                  'docs: game dumps in a git-ignored .gamedump/<App>/ folder',
                  'chore(release)!: version 0.1.1',
                  'feat(android/gamepad): draggable menu button\n\nHidden while a controller is attached.\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>',
                  'refactor(porter)!: profiles load from one folder\n\nBREAKING CHANGE: DM_GAMES_DIR replaces DM_PROFILES',
                  'perf: x\n\nhttps://example.com/' + 'a' * 120,
                  'Merge branch \'main\' into xc3', 'Revert "fix(vk): runs of rasterizer-discard draws"', 'fixup! fix(vk): x', ''):
            self.assertEqual(errs(m), [], m)

    def test_refused(self):
        for m, why in (('Vulkan backend: a transfer read (XC3 grass flicker)', 'the header must be'),
                       ('vulkan: a transfer read (XC3 grass flicker)', 'is not a type'),
                       ('Version 0.1.1', 'the header must be'),
                       ('Fix: crash on start', 'use "fix"'),
                       ('fix:crash on start', 'the header must be'),
                       ('fix: ', 'the header must be'),
                       ('fix:  two spaces', 'extra spaces'),
                       ('fix(): empty scope', 'scope'),
                       ('fix: ends with a full stop.', 'ends with "."'),
                       ('feat: ' + 'x' * 100, 'the header is 106'),
                       ('fix: x\nbody right under the header', 'one blank line'),
                       ('fix: x\n\n' + 'word ' * 25, 'line 3 is'),
                       ('feat!: x\n\nbreaking change: lower case', 'BREAKING CHANGE')):
            e = errs(m)
            self.assertTrue(any(why in s for s in e), f'{m!r} -> {e}')

    def test_cleaned_like_git(self):
        verbose = 'fix: x\n\n# Please enter the commit message\n# ------------------------ >8 ------------------------\ndiff --git a b\n+' + 'y' * 200
        self.assertEqual(cm.clean(verbose), ['fix: x'])
        self.assertEqual(cm.clean('; c\nfix: x\n', ';'), ['fix: x'])

    def test_exit_status(self):
        for text, rc in (('fix: x\n', 0), ('wip\n', 1)):
            with tempfile.NamedTemporaryFile('w', suffix='.txt', delete=False, encoding='utf-8') as f: f.write(text)
            self.assertEqual(subprocess.run([sys.executable, str(HOOK), f.name], capture_output=True).returncode, rc, text)
            Path(f.name).unlink()


if __name__ == '__main__':
    unittest.main()
