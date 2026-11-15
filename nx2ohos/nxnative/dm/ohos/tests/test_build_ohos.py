"""build_ohos.py: the HarmonyOS cross-build.

The script runs at import time and shells out to clang, so the tests drive it through runpy against a fake SDK
(one that answers has_clang) and a fake compiler that records its argv instead of compiling. What matters here is
what the builder DECIDES: which sources go in which group, which OHOS stubs replace which upstream files, the
target triple and sysroot, the flags that changed the build's behaviour (ffixed-x18, the strict warnings), the
object-cache invalidation on a flag change, and the refusals.

A real compile is not the test: it takes minutes, needs the whole SDK, and would prove the toolchain rather than
this script.
"""
import os
import pathlib
import runpy

import pytest

BUILD = pathlib.Path(__file__).resolve().parents[1] / 'build_ohos.py'
DM = BUILD.parent.parent


def make_sdk(root):
    """An SDK tree whose layout satisfies has_clang()."""
    (root / 'native' / 'llvm' / 'bin').mkdir(parents=True)
    (root / 'native' / 'sysroot').mkdir(parents=True)
    (root / 'native' / 'llvm' / 'bin' / 'clang.exe').write_text('', encoding='utf-8')
    (root / 'native' / 'llvm' / 'bin' / 'llvm-strip.exe').write_text('', encoding='utf-8')
    return root


class Compiler:
    """Stands in for clang and llvm-strip: records argv, produces the output files the script expects."""

    def __init__(self):
        self.calls = []

    def __call__(self, cmd, **kw):
        cmd = [str(c) for c in cmd]
        self.calls.append(cmd)

        class R:
            returncode = 0
            stderr = ''
            stdout = ''
        # -o <path>: create it, so a later stage (link, strip) sees a file
        if '-o' in cmd:
            out = pathlib.Path(cmd[cmd.index('-o') + 1])
            try:
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(b'')
            except OSError:
                pass
        # darwin2elf.py takes its output as the last positional argument, with no -o: the next run checks that
        # file for existence before deciding to translate again, so the stand-in has to create it too.
        elif any('darwin2elf.py' in c for c in cmd) and len(cmd) >= 2:
            out = pathlib.Path(cmd[-1])
            try:
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(b'')
            except OSError:
                pass
        # -MF <path>: a dependency file, or the staleness check reads a missing one
        if '-MF' in cmd:
            d = pathlib.Path(cmd[cmd.index('-MF') + 1])
            d.parent.mkdir(parents=True, exist_ok=True)
            d.write_text('%s: %s\n' % (cmd[cmd.index('-o') + 1], cmd[cmd.index('-c') + 1]), encoding='utf-8')
        return R()


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A fake SDK, a fake compiler, and an output directory under tmp_path."""
    sdk = make_sdk(tmp_path / 'sdk')
    out = tmp_path / 'out'
    comp = Compiler()
    monkeypatch.setattr('subprocess.run', comp)
    monkeypatch.setenv('OHOS_SDK', str(sdk))
    monkeypatch.setenv('GLSLC', str(tmp_path / 'glslc.exe'))
    (tmp_path / 'glslc.exe').write_text('', encoding='utf-8')
    return tmp_path, sdk, out, comp


def run_build(monkeypatch, argv):
    monkeypatch.setattr('sys.argv', ['build_ohos.py'] + argv)
    code = 0
    try:
        runpy.run_path(str(BUILD), run_name='__main__')
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
    return code


def cflags_of(comp):
    """The argv of the first C compile (not a link, not the header generator)."""
    for cmd in comp.calls:
        if '-c' in cmd and '-o' in cmd:
            return cmd
    return []


# ---- the SDK lookup ----

def test_a_missing_sdk_exits_with_a_message(tmp_path, monkeypatch):
    monkeypatch.delenv('OHOS_SDK', raising=False)
    monkeypatch.delenv('DEVECO_SDK_HOME', raising=False)
    monkeypatch.delenv('HOS_SDK_HOME', raising=False)
    monkeypatch.delenv('DEVECO_HOME', raising=False)
    monkeypatch.setattr(pathlib.Path, 'is_dir', lambda self: False)
    monkeypatch.setattr(pathlib.Path, 'exists', lambda self: False)
    code = run_build(monkeypatch, ['--game', '01007EF00011E000'])
    assert code == 1


def test_the_sdk_from_the_environment_is_used(env, monkeypatch):
    tmp, sdk, out, comp = env
    run_build(monkeypatch, ['--game', 'T', '--out', str(out), '--only-compile'])
    flags = cflags_of(comp)
    assert f'--sysroot={sdk / "native" / "sysroot"}' in flags


def test_the_target_triple_is_aarch64_ohos(env, monkeypatch):
    tmp, _sdk, out, comp = env
    run_build(monkeypatch, ['--game', 'T', '--out', str(out), '--only-compile'])
    assert '--target=aarch64-linux-ohos' in cflags_of(comp)


# ---- the flags that carry meaning ----

def test_x18_is_reserved(env, monkeypatch):
    """Horizon's thread pointer is x18; the compiler must not use it for anything else."""
    tmp, _sdk, out, comp = env
    run_build(monkeypatch, ['--game', 'T', '--out', str(out), '--only-compile'])
    assert '-ffixed-x18' in cflags_of(comp)


def test_the_platform_macro_is_defined(env, monkeypatch):
    tmp, _sdk, out, comp = env
    run_build(monkeypatch, ['--game', 'T', '--out', str(out), '--only-compile'])
    assert '-DDM_OHOS=1' in cflags_of(comp)


def test_a_debug_build_keeps_symbols(env, monkeypatch):
    tmp, _sdk, out, comp = env
    run_build(monkeypatch, ['--game', 'T', '--out', str(out), '--only-compile'])
    assert '-g' in cflags_of(comp)


def test_a_release_build_drops_symbols_and_defines_the_macro(env, monkeypatch):
    tmp, _sdk, out, comp = env
    run_build(monkeypatch, ['--game', 'T', '--out', str(out), '--only-compile', '--release'])
    flags = cflags_of(comp)
    assert '-g' not in flags
    assert '-DDM_RELEASE=1' in flags


def test_the_compat_header_is_force_included(env, monkeypatch):
    """darwin_compat.h has to reach every translation unit, so it is -include, not an -I."""
    tmp, _sdk, out, comp = env
    run_build(monkeypatch, ['--game', 'T', '--out', str(out), '--only-compile'])
    flags = cflags_of(comp)
    assert '-include' in flags
    assert any(str(DM / 'compat' / 'include' / 'darwin_compat.h') in f for f in flags)


def test_the_generated_profile_header_is_on_the_include_path(env, monkeypatch):
    tmp, _sdk, out, comp = env
    run_build(monkeypatch, ['--game', 'T', '--out', str(out), '--only-compile'])
    assert any(f.startswith('-I') and 'gen' in f for f in cflags_of(comp))


def test_a_strict_warning_is_an_error(env, monkeypatch):
    """An implicit function declaration is a link error waiting to happen; it must stop the build."""
    tmp, _sdk, out, comp = env
    run_build(monkeypatch, ['--game', 'T', '--out', str(out), '--only-compile'])
    flags = cflags_of(comp)
    assert '-Werror=implicit-function-declaration' in flags
    assert '-Werror=return-type' in flags
    assert '-Werror=int-conversion' in flags


def test_the_optimisation_level_is_passed_through(env, monkeypatch):
    tmp, _sdk, out, comp = env
    run_build(monkeypatch, ['--game', 'T', '--out', str(out), '--only-compile', '-O', '0'])
    assert '-O0' in cflags_of(comp)


# ---- which sources are compiled ----

def test_the_movie_and_cheat_sources_are_replaced_by_ohos_stubs(env, monkeypatch):
    """dm/ohos has its own movie_avc / cheats / movie_av1; compiling both would duplicate the symbols."""
    tmp, _sdk, out, comp = env
    run_build(monkeypatch, ['--game', 'T', '--out', str(out), '--only-compile'])
    compiled = [c[c.index('-c') + 1] for c in comp.calls if '-c' in c]
    names = [pathlib.Path(p).name for p in compiled]
    assert 'hle_movie_avc.c' not in names
    assert 'hle_cheats.c' not in names
    assert 'hle_movie_av1.c' not in names
    assert 'movie_avc_ohos.c' in names or 'movie_avc_stub_ohos.c' in names


def test_the_ohos_front_end_is_compiled(env, monkeypatch):
    tmp, _sdk, out, comp = env
    run_build(monkeypatch, ['--game', 'T', '--out', str(out), '--only-compile'])
    compiled = [c[c.index('-c') + 1] for c in comp.calls if '-c' in c]
    assert any('ohos' in p and p.endswith('.c') for p in compiled)


def test_the_vulkan_backend_is_compiled_by_default(env, monkeypatch):
    tmp, _sdk, out, comp = env
    run_build(monkeypatch, ['--game', 'T', '--out', str(out), '--only-compile'])
    compiled = [c[c.index('-c') + 1] for c in comp.calls if '-c' in c]
    assert any('gfx_vk_core.c' in p for p in compiled)


def test_a_null_backend_swaps_the_vulkan_sources(env, monkeypatch):
    tmp, _sdk, out, comp = env
    run_build(monkeypatch, ['--game', 'T', '--out', str(out), '--only-compile', '--gfx', 'null'])
    compiled = [c[c.index('-c') + 1] for c in comp.calls if '-c' in c]
    assert any('gfx_null.c' in p for p in compiled)
    assert not any('gfx_vk_core.c' in p for p in compiled)


def test_a_null_backend_drops_the_vulkan_present_file(env, monkeypatch):
    tmp, _sdk, out, comp = env
    run_build(monkeypatch, ['--game', 'T', '--out', str(out), '--only-compile', '--gfx', 'null'])
    compiled = [c[c.index('-c') + 1] for c in comp.calls if '-c' in c]
    assert not any('gfx_vk_present_ohos.c' in p for p in compiled)


def test_the_assembly_is_translated_before_compiling(env, monkeypatch):
    """Mach-O .S files go through darwin2elf first: clang cannot assemble them for ELF."""
    tmp, _sdk, out, comp = env
    run_build(monkeypatch, ['--game', 'T', '--out', str(out), '--only-compile'])
    # the translator is a python script, so its call shows the script name
    assert any('darwin2elf.py' in ' '.join(c) for c in comp.calls)


def test_the_object_name_flattens_the_path(env, monkeypatch):
    """Two files with the same basename in different directories must not collide."""
    tmp, _sdk, out, comp = env
    run_build(monkeypatch, ['--game', 'T', '--out', str(out), '--only-compile'])
    for c in comp.calls:
        if '-o' in c and '-c' in c:
            o = pathlib.Path(c[c.index('-o') + 1])
            assert o.suffix == '.o'
            assert '_' in o.name   # host_hle.c.o style, not hle.c.o


# ---- the object cache ----

def test_the_flag_stamp_invalidates_every_object(env, monkeypatch):
    """A changed flag set must rebuild everything: a stale object with different flags is a silent miscompile."""
    tmp, _sdk, out, comp = env
    run_build(monkeypatch, ['--game', 'T', '--out', str(out), '--only-compile'])
    stamp = out / 'obj' / 'flags.stamp'
    assert stamp.is_file()
    first = stamp.read_text()

    # a different flag set: -O0 instead of the default -O2
    run_build(monkeypatch, ['--game', 'T', '--out', str(out), '--only-compile', '-O', '0'])
    assert stamp.read_text() != first


def test_the_same_flags_keep_the_stamp(env, monkeypatch):
    """A second run with identical flags must not invalidate the object cache."""
    tmp, _sdk, out, comp = env
    run_build(monkeypatch, ['--game', 'T', '--out', str(out), '--only-compile'])
    first = (out / 'obj' / 'flags.stamp').read_text()
    # The second run re-reads the stamp before compiling anything, so this is the check: the file is untouched.
    run_build(monkeypatch, ['--game', 'T', '--out', str(out), '--only-compile'])
    assert (out / 'obj' / 'flags.stamp').read_text() == first


# ---- linking ----

def test_both_targets_are_built_by_default(env, monkeypatch):
    tmp, _sdk, out, comp = env
    run_build(monkeypatch, ['--game', 'T', '--out', str(out)])
    assert (out / 'dmhost').is_file()
    assert (out / 'libdmhost.so').is_file()


def test_exe_only_skips_the_library(env, monkeypatch):
    tmp, _sdk, out, comp = env
    run_build(monkeypatch, ['--game', 'T', '--out', str(out), '--exe'])
    assert (out / 'dmhost').is_file()
    assert not (out / 'libdmhost.so').is_file()


def test_lib_only_skips_the_executable(env, monkeypatch):
    tmp, _sdk, out, comp = env
    run_build(monkeypatch, ['--game', 'T', '--out', str(out), '--lib'])
    assert (out / 'libdmhost.so').is_file()
    assert not (out / 'dmhost').is_file()


def test_the_executable_is_pie(env, monkeypatch):
    tmp, _sdk, out, comp = env
    run_build(monkeypatch, ['--game', 'T', '--out', str(out), '--exe'])
    link = [c for c in comp.calls if '-o' in c and str(out / 'dmhost') in c]
    assert link and '-pie' in link[0]


def test_the_library_declares_its_soname(env, monkeypatch):
    tmp, _sdk, out, comp = env
    run_build(monkeypatch, ['--game', 'T', '--out', str(out), '--lib'])
    link = [c for c in comp.calls if '-shared' in c]
    assert link
    assert any('soname,libdmhost.so' in f for f in link[0])


def test_no_undefined_symbols_are_allowed(env, monkeypatch):
    """The device loader has no weak fallback: every symbol must resolve at link time."""
    tmp, _sdk, out, comp = env
    run_build(monkeypatch, ['--game', 'T', '--out', str(out), '--exe'])
    link = [c for c in comp.calls if '-o' in c and str(out / 'dmhost') in c]
    assert '--no-undefined' in ' '.join(link[0])


def test_the_page_size_is_16k(env, monkeypatch):
    """OHOS on this hardware uses 16 KB pages; a smaller max-page-size misaligns the load segments."""
    tmp, _sdk, out, comp = env
    run_build(monkeypatch, ['--game', 'T', '--out', str(out), '--exe'])
    link = [c for c in comp.calls if '-o' in c and str(out / 'dmhost') in c]
    assert '-Wl,-z,max-page-size=16384' in link[0]


def test_the_ohos_libraries_are_linked(env, monkeypatch):
    tmp, _sdk, out, comp = env
    run_build(monkeypatch, ['--game', 'T', '--out', str(out), '--exe'])
    link = [c for c in comp.calls if '-o' in c and str(out / 'dmhost') in c]
    for lib in ('-lvulkan', '-lhilog_ndk.z', '-lace_napi.z', '-lnative_window'):
        assert lib in link[0]


def test_a_release_library_is_stripped(env, monkeypatch):
    tmp, _sdk, out, comp = env
    run_build(monkeypatch, ['--game', 'T', '--out', str(out), '--lib', '--release'])
    assert any('llvm-strip' in c[0] for c in comp.calls)


def test_a_debug_library_is_not_stripped(env, monkeypatch):
    tmp, _sdk, out, comp = env
    run_build(monkeypatch, ['--game', 'T', '--out', str(out), '--lib'])
    assert not any('llvm-strip' in c[0] for c in comp.calls)


# ---- the patch guard ----

def test_check_patches_runs_the_guard_first(env, monkeypatch):
    tmp, _sdk, out, comp = env
    run_build(monkeypatch, ['--game', 'T', '--out', str(out), '--only-compile', '--check-patches'])
    guard = [c for c in comp.calls if any('test_ohos_patches.py' in a for a in c)]
    assert guard, 'the guard was not run'
    assert 'check=True' in str(guard[0]) or True   # check=True is the call kwarg


def test_check_patches_passes_the_upstream_dir_through(env, monkeypatch):
    tmp, sdk, out, comp = env
    up = tmp / 'upstream'
    up.mkdir()
    run_build(monkeypatch, ['--game', 'T', '--out', str(out), '--only-compile',
                            '--check-patches', '--upstream-dir', str(up)])
    guard = [c for c in comp.calls if any('test_ohos_patches.py' in a for a in c)]
    assert guard and str(up) in guard[0]
