# docs/ARCHITECTURE.md's layer rules on the committed tree: tests/arch_rules.py prints each check
import subprocess, sys, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent


class Architecture(unittest.TestCase):
    def test_rules(self):
        r = subprocess.run([sys.executable, str(HERE / 'arch_rules.py'), str(HERE.parent)], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == '__main__':
    unittest.main()
