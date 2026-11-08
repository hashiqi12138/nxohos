#!/usr/bin/env python3
# run_unit_tests.py — T0 统一入口：一次跑全部 PC 可跑测试（CI 与本地同一入口）
#
# 组成（无 pytest 依赖，仅标准库）：
#   1. 编译并运行 C 单测 t_probe_units（probe_json.c + dmtest.c + t_probe_units.c，
#      -DDM_OHOS_HOST_TEST，PC 编译器自动发现）
#   2. 各 python 验证工具的 --selftest
#      （verify_pixel / assert_hilog / collect_frames / verify_report）
#
# 退出码：0 = 全绿；1 = 有失败（各项失败明细见输出中的 [FAIL] 行）。
# 用法：python tools/verify/run_unit_tests.py
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))       # tools/verify/
ROOT = os.path.dirname(os.path.dirname(HERE))           # ohos-port/
PROBE = os.path.join(ROOT, "probe")
TESTS = os.path.join(PROBE, "tests")
OUT = os.path.join(ROOT, "out", "tests")

PY_TOOLS = ["verify_pixel.py", "assert_hilog.py", "collect_frames.py",
            "verify_report.py", "reassemble_hilog_json.py"]


def find_host_cc():
    cc = os.environ.get("CC")
    candidates = ([cc] if cc else []) + ["clang", "gcc"]
    msys2 = [
        r"E:\build-tool\msys2\mingw64\bin\clang.exe",
        r"E:\build-tool\msys2\mingw64\bin\gcc.exe",
    ]
    for c in candidates:
        if c and shutil.which(c):
            return c
    for c in msys2:
        if os.path.isfile(c):
            return c
    return None


def run_c_unit_tests(cc):
    os.makedirs(OUT, exist_ok=True)
    exe = os.path.join(OUT, "t_probe_units.exe" if os.name == "nt" else "t_probe_units")
    srcs = [
        os.path.join(TESTS, "t_probe_units.c"),
        os.path.join(PROBE, "probe_json.c"),
        os.path.join(TESTS, "dmtest.c"),
    ]
    cmd = [cc, "-O1", "-Wall", "-Wextra", "-std=gnu11", "-DDM_OHOS_HOST_TEST",
           "-I" + PROBE, "-I" + TESTS, *srcs, "-o", exe]
    print("+ " + " ".join(cmd))
    r = subprocess.run(cmd)
    if r.returncode != 0:
        print("[FAIL] t_probe_units: compile error")
        return 1
    r = subprocess.run([exe])
    if r.returncode != 0:
        print("[FAIL] t_probe_units: %d case(s) failed" % r.returncode)
        return 1
    print("[ OK ] t_probe_units")
    return 0


def run_py_selftests():
    failed = 0
    for tool in PY_TOOLS:
        path = os.path.join(HERE, tool)
        print("+ python %s --selftest" % tool)
        r = subprocess.run([sys.executable, path, "--selftest"])
        if r.returncode != 0:
            print("[FAIL] %s --selftest" % tool)
            failed += 1
        else:
            print("[ OK ] %s --selftest" % tool)
    return failed


def main():
    failures = 0
    cc = find_host_cc()
    if not cc:
        print("[FAIL] no host compiler found (set CC or install clang/gcc)")
        failures += 1
    else:
        print("host CC: %s" % cc)
        failures += run_c_unit_tests(cc)
    failures += run_py_selftests()

    print("\nT0 summary: %s" % ("ALL GREEN" if failures == 0 else
                                "%d failure(s)" % failures))
    return failures


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
