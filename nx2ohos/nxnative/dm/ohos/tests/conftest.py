"""Shared fixtures for the OHOS port's own tools (dm/tools and dm/ohos).

These are separate from dm/tools/tests, which covers the upstream tooling: the files under test here are the ones
this port added (pack_game, pack_server, devpush, mkhap, daily_sync and friends), and they are imported by path
because the repository root is not a package.

Every fixture keeps its work in tmp_path, so a test never touches the checkout or a real device.
"""
import importlib.util
import pathlib
import sys

import pytest

# conftest.py lives at dm/ohos/tests/conftest.py, so the repo root is three levels up.
REPO = pathlib.Path(__file__).resolve().parents[3]
TOOLS = REPO / 'dm' / 'tools'
OHOS = REPO / 'dm' / 'ohos'


def load(module_path: pathlib.Path):
    """Import a module from its file path, so a test can reach a script that is not on sys.path."""
    name = 'ohos_test_' + module_path.stem
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, module_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope='session')
def repo() -> pathlib.Path:
    return REPO


@pytest.fixture(scope='session')
def tools_dir() -> pathlib.Path:
    return TOOLS


@pytest.fixture(scope='session')
def ohos_dir() -> pathlib.Path:
    return OHOS


@pytest.fixture
def pack_game():
    return load(TOOLS / 'pack_game.py')


@pytest.fixture
def pack_server():
    return load(TOOLS / 'pack_server.py')


@pytest.fixture
def gen_manifest():
    return load(TOOLS / 'gen_pack_manifest.py')


@pytest.fixture
def fix_dashes():
    return load(TOOLS / 'fix_utf8_dashes.py')


@pytest.fixture
def daily_sync():
    return load(TOOLS / 'daily_sync.py')


@pytest.fixture
def sync_upstream():
    return load(TOOLS / 'sync_upstream.py')


@pytest.fixture
def export_patches():
    return load(TOOLS / 'export_ohos_patches.py')


@pytest.fixture
def guard():
    return load(TOOLS / 'test_ohos_patches.py')


@pytest.fixture
def build_ohos():
    return load(OHOS / 'build_ohos.py')


@pytest.fixture
def devpush():
    return load(OHOS / 'devpush.py')


@pytest.fixture
def e2e_smoke():
    return load(OHOS / 'e2e_smoke.py')


@pytest.fixture
def mkhap():
    return load(OHOS / 'mkhap.py')
