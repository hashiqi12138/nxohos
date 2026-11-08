#!/usr/bin/env python3
# build_probe.py — M0.1 OHOS SDK 工具链构建（B 文档 §M0.1 伪代码落地）
#
# 用法：
#   python build_probe.py                 # 设备构建：需要环境变量 OHOS_SDK
#   python build_probe.py --host-check    # PC 侧：对 t_probe_units.c 做 -fsyntax-only
#                                         # （TC0-1-1 的 Windows/Linux 半边）
#
# 产物（设备构建）：
#   out/libs/arm64/libprobe.so            探针 NAPI 库（hap 打包进 native 库目录）
#   out/bin/t_mini_nso                    重写码真机测试可执行（hdc shell 直跑）
#   out/gen/mini_nso_blob.h               由 mini_nso.S 生成的机码头（probe_regs.c 用）
#
# 确定性构建（TC0-1-1）：跨 OS 复现产物 —— -ffile-prefix-map 归一化路径、
# -fno-record-gcc-switches 抹命令行、-D__FILE__="." 固定文件宏。
import argparse
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))          # probe/
ROOT = os.path.dirname(HERE)                               # ohos-port/
OUT = os.path.join(ROOT, "out")
GEN = os.path.join(OUT, "gen")

DETERMINISTIC = [
    "-ffile-prefix-map={cwd}=.".format(cwd=ROOT),
    "-fno-record-gcc-switches",
    "-Wno-builtin-macro-redefined",
    '-D__FILE__="."',
]

# M0.5g 宿主侧金丝雀依赖：宿主代码不得占用 x18（B 文档 M1.1 同款参数）
BASE_FLAGS = [
    "-march=armv8-a",          # 保守基线；按真机实测再评估升级（勿开 SVE）
    "-ffixed-x18",
    "-O2",
    "-Wall",
    "-Wextra",
] + DETERMINISTIC


def run(cmd, env=None):
    print("+ " + " ".join(cmd))
    return subprocess.run(cmd, check=True, env=env).returncode


def find_host_cc():
    """PC 侧编译器自动发现：CC 环境变量 → clang → gcc → msys2 常见路径。"""
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


def build_stamp():
    """源码内容指纹 → 写进报告 meta.build。
    用内容哈希而非时间戳：既能在报告里区分"设备上跑的是哪一版"，
    又不破坏 TC0-1-1 的确定性构建（同源码两次构建产物一致）。"""
    import hashlib
    h = hashlib.sha256()
    for name in sorted(os.listdir(HERE)):
        if name.endswith((".c", ".h")) and os.path.isfile(os.path.join(HERE, name)):
            with open(os.path.join(HERE, name), "rb") as f:
                h.update(name.encode() + b"\0" + f.read())
    return h.hexdigest()[:12]


def sdk_tool(bindir, name):
    """SDK 工具定位：Windows 上可执行带 .exe 后缀，先试 .exe 再试原名。"""
    for cand in (name + ".exe", name):
        p = os.path.join(bindir, cand)
        if os.path.isfile(p):
            return p
    return os.path.join(bindir, name)


def build_device():
    sdk = os.environ.get("OHOS_SDK")
    if not sdk:
        print("ERROR: OHOS_SDK not set (e.g. /opt/ohos-sdk)")
        return 1
    bindir = os.path.join(sdk, "native", "llvm", "bin")
    cc = sdk_tool(bindir, "clang")
    nm = sdk_tool(bindir, "llvm-nm")
    objcopy = sdk_tool(bindir, "llvm-objcopy")
    for tool in (cc, nm, objcopy):
        if not os.path.isfile(tool):
            print("ERROR: tool not found: %s" % tool)
            return 1
    sysroot = os.path.join(sdk, "native", "sysroot")

    target = [
        "--target=aarch64-linux-ohos",
        "--sysroot=" + sysroot,
    ]
    os.makedirs(GEN, exist_ok=True)
    os.makedirs(os.path.join(OUT, "libs", "arm64"), exist_ok=True)
    os.makedirs(os.path.join(OUT, "bin"), exist_ok=True)

    # ---------- 1) mini NSO 机码 → blob 头（probe_regs.c 的 M0.5g 输入） ----------
    mini_asm = os.path.join(HERE, "tests", "mini_nso", "mini_nso.S")
    mini_obj = os.path.join(OUT, "gen", "mini_nso.o")
    mini_bin = os.path.join(OUT, "gen", "mini_nso.bin")
    run([cc] + target + BASE_FLAGS + ["-c", mini_asm, "-o", mini_obj])
    run([objcopy, "-O", "binary", "--only-section=.text", mini_obj, mini_bin])

    # 从符号表生成入口偏移（键名契约见 mini_host.h；无符号解析依赖第三方库）
    syms = {}
    out = subprocess.run([nm, "-S", "--defined-only", mini_obj],
                         capture_output=True, text=True, check=True).stdout
    for line in out.splitlines():
        parts = line.split()
        # 格式：<addr> <size> <type> <name>
        if len(parts) == 4 and parts[3].startswith("mini_"):
            syms[parts[3]] = int(parts[0], 16)
    need = ["mini_svc_fn", "mini_x18_fn", "mini_tls_fn", "mini_timer_fn"]
    if any(s not in syms for s in need):
        print("ERROR: mini_nso symbols missing: %s" % out)
        return 1
    code = open(mini_bin, "rb").read()
    with open(os.path.join(GEN, "mini_nso_blob.h"), "w", newline="\n") as f:
        f.write("/* 由 build_probe.py 自动生成 —— mini_nso.S 的 .text 机码。勿手改。 */\n")
        f.write("#ifndef MINI_NSO_BLOB_H\n#define MINI_NSO_BLOB_H\n#include <stdint.h>\n")
        f.write("static const unsigned char mini_nso_code[] = {\n")
        for i in range(0, len(code), 12):
            f.write("    " + ", ".join("0x%02x" % b for b in code[i:i + 12]) + ",\n")
        f.write("};\nstatic const unsigned int mini_nso_code_len = %d;\n" % len(code))
        for key, sym in [("MINI_OFF_SVC", "mini_svc_fn"),
                         ("MINI_OFF_X18", "mini_x18_fn"),
                         ("MINI_OFF_TLS", "mini_tls_fn"),
                         ("MINI_OFF_TIMER", "mini_timer_fn")]:
            f.write("#define %s 0x%x\n" % (key, syms[sym]))
        f.write("#endif\n")
    print("mini_nso blob: %d bytes, entries %s" % (len(code), syms))

    # ---------- 2) libprobe.so ----------
    srcs = [os.path.join(HERE, s) for s in
            ["probe_main.c", "probe_regs.c", "probe_mem.c",
             "probe_vk.c", "probe_json.c", "mini_host.c"]]
    so = os.path.join(OUT, "libs", "arm64", "libprobe.so")
    stamp = build_stamp()
    run([cc] + target + BASE_FLAGS + [
        "-std=gnu11",
        '-DDM_PROBE_BUILD="%s"' % stamp,
        "-I" + HERE,
        "-I" + GEN,
        "-shared", "-fPIC",
        "-Wl,--no-undefined",   # 声明未实现（如 gate 缺定义）在构建期即报错
        *srcs,
        "-o", so,
        "-lvulkan", "-lhilog_ndk.z", "-lace_ndk.z", "-lace_napi.z",
        "-ldeviceinfo_ndk.z",   # OH_GetProductModel / OH_GetOSFullName
        "-lnative_window",      # OH_NativeWindow_CreateNativeWindowFromSurfaceId
    ])
    print("built: %s" % so)

    # ---------- 2.5) libmininso.so：AOT 形态验证（M0.5h） ----------
    # 真机上运行时申请可执行页的六条路径全被拒，重写器只能 AOT：离线重写 →
    # 产出真实 .so → 随 hap 打包 → 系统加载器按 .so 正常映射成 RX。本库就是
    # 这个形态的最小样本：mini_nso.S 的机码是它的正文，probe 用 dlopen 加载。
    mini_so = os.path.join(OUT, "libs", "arm64", "libmininso.so")
    run([cc] + target + BASE_FLAGS + [
        "-shared", "-fPIC",
        mini_obj,
        "-o", mini_so,
    ])
    print("built: %s" % mini_so)

    # ---------- 3) t_mini_nso 真机可执行（静态链接，hdc shell 直跑） ----------
    tsrc = os.path.join(HERE, "tests", "t_mini_nso.c")
    dtest = os.path.join(HERE, "tests", "dmtest.c")
    tbin = os.path.join(OUT, "bin", "t_mini_nso")
    run([cc] + target + BASE_FLAGS + [
        "-std=gnu11",
        "-I" + HERE + "/tests",
        "-I" + HERE,
        "-I" + GEN,
        tsrc, dtest, os.path.join(HERE, "mini_host.c"), mini_obj,
        "-o", tbin, "-static",
    ])
    print("built: %s" % tbin)
    return 0


def host_check():
    """PC 侧语法检查（B 文档 §M0.1 的 host 半边；不执行、不产出二进制）。"""
    cc = find_host_cc()
    if not cc:
        print("ERROR: no host compiler (set CC or install clang/gcc)")
        return 1
    tsrc = os.path.join(HERE, "tests", "t_probe_units.c")
    return run([cc, "-fsyntax-only", "-std=gnu11", "-Wall", "-Wextra",
                "-DDM_OHOS_HOST_TEST", "-I" + HERE, "-I" + os.path.join(HERE, "tests"),
                tsrc])


def main():
    ap = argparse.ArgumentParser(description="M0 probe builder")
    ap.add_argument("--host-check", action="store_true",
                    help="PC side syntax-only check of t_probe_units.c")
    args = ap.parse_args()
    if args.host_check:
        return host_check()
    return build_device()


if __name__ == "__main__":
    sys.exit(main())
