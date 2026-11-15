"""The files this port owns, and whether each has a test file yet.

Used by run_tests.py's report and by the coverage gate's own test: a file added to the port without tests shows up
here as a gap rather than as an unnoticed hole in the percentage.
"""
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[3]

# path under the repo -> the test module that covers it
OWNED = {
    'dm/tools/pack_game.py': 'test_pack_game.py',
    'dm/tools/pack_server.py': 'test_pack_server.py',
    'dm/tools/gen_pack_manifest.py': 'test_gen_pack_manifest.py',
    'dm/tools/fix_utf8_dashes.py': 'test_fix_utf8_dashes.py',
    'dm/tools/daily_sync.py': 'test_daily_sync.py',
    'dm/tools/export_ohos_patches.py': 'test_export_ohos_patches.py',
    'dm/tools/test_ohos_patches.py': 'test_test_ohos_patches.py',
    'dm/ohos/build_ohos.py': 'test_build_ohos.py',
    'dm/ohos/devpush.py': 'test_devpush.py',
    'dm/ohos/e2e_smoke.py': 'test_e2e_smoke.py',
    'dm/ohos/mkhap.py': 'test_mkhap.py',
}
