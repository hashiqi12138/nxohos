#!/usr/bin/env python3
# build_hap.py — 构建探针 hap（ProbeAbility）
#
# 用法：
#   python probe/build_hap.py                # 构建 unsigned hap → out/probe-unsigned.hap
#   python probe/build_hap.py --keep-build   # 保留中间构建目录便于排查
#
# 为什么需要这个脚本（两条硬约束，实测）：
#   1. hvigor 拒绝含非 ASCII 字符的工程路径（本仓路径含中文），必须复制到
#      纯 ASCII 临时目录构建；
#   2. libprobe.so 要先由 build_probe.py 产出，再投放到 entry/libs/arm64-v8a/
#      才会被打进 hap 的 libs/arm64-v8a/。
#
# 签名（云调试/真机安装必需）本脚本不做：
#   DevEco 自动签名的密钥库口令保存在 IDE 安全存储里（不可导出），
#   请在 DevEco Studio 打开 probe/hap → Project Structure → Signing Configs
#   → 勾选 Automatically generate signature → 再 Build。
import argparse
import glob
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))   # probe/
ROOT = os.path.dirname(HERE)                        # ohos-port/
HAP_SRC = os.path.join(HERE, "hap")
SO = os.path.join(ROOT, "out", "libs", "arm64", "libprobe.so")
OUT_HAP = os.path.join(ROOT, "out", "probe-unsigned.hap")
OUT_HAP_SIGNED = os.path.join(ROOT, "out", "probe-signed.hap")

DEVECO_CANDIDATES = [
    os.environ.get("DEVECO_HOME", ""),
    r"D:\dev-echo\DevEco Studio",
    r"C:\Program Files\Huawei\DevEco Studio",
]


def find_deveco():
    for p in DEVECO_CANDIDATES:
        if p and os.path.isdir(os.path.join(p, "tools", "hvigor", "bin")):
            return p
    return None


def pick_build_dir():
    """选一个 hvigor 能接受的构建目录。
    hvigor 路径白名单：字母/数字/连字符/下划线/点/英文括号/空格/@ ——
    tempfile 常给出 C:\\Users\\LIUJIE~1\\... （含 ~）会被拒，故优先用盘根。"""
    cand = os.environ.get("DM_HAP_BUILD_DIR")
    if cand:
        return cand
    for drive in ("F:", "D:", "C:"):
        root = drive + os.sep
        if os.path.isdir(root):
            return os.path.join(root, "ohos_build_hap")
    return os.path.join(tempfile.gettempdir(), "ohos_build_hap")


def main():
    ap = argparse.ArgumentParser(description="build probe hap")
    ap.add_argument("--keep-build", action="store_true",
                    help="keep the ASCII build directory")
    args = ap.parse_args()

    deveco = find_deveco()
    if not deveco:
        print("ERROR: DevEco Studio not found (set DEVECO_HOME)")
        return 1
    hvigorw = os.path.join(deveco, "tools", "hvigor", "bin", "hvigorw.bat")
    node = os.path.join(deveco, "tools", "node")
    if not os.path.isfile(SO):
        print("ERROR: %s missing — run probe/build_probe.py first" % SO)
        return 1

    # 1) 工程复制到 ASCII 路径（hvigor 路径限制）
    build_root = pick_build_dir()
    if os.path.isdir(build_root):
        shutil.rmtree(build_root, ignore_errors=True)
    shutil.copytree(HAP_SRC, build_root)

    # 1.5) 递增 versionCode：同版本 hap 在云调试上会被
    #      "已经存在相同版本应用，请确认" 拦下，装不上新代码（实测）。
    app_json = os.path.join(build_root, "AppScope", "app.json5")
    with open(app_json, "r", encoding="utf-8") as f:
        txt = f.read()
    import re
    import time
    vc = 1000000 + int(time.time()) % 100000
    txt = re.sub(r'"versionCode"\s*:\s*\d+', '"versionCode": %d' % vc, txt, count=1)
    with open(app_json, "w", encoding="utf-8") as f:
        f.write(txt)
    print("versionCode -> %d" % vc)

    # 2) 投放 native 库
    libdir = os.path.join(build_root, "entry", "libs", "arm64-v8a")
    os.makedirs(libdir, exist_ok=True)
    shutil.copy2(SO, os.path.join(libdir, "libprobe.so"))
    mini_so = os.path.join(ROOT, "out", "libs", "arm64", "libmininso.so")
    if os.path.isfile(mini_so):
        shutil.copy2(mini_so, os.path.join(libdir, "libmininso.so"))

    # 3) 构建
    env = dict(os.environ)
    env["DEVECO_SDK_HOME"] = os.path.join(deveco, "sdk")
    env["PATH"] = node + os.pathsep + env.get("PATH", "")
    # 签名需要 HmacPBESHA256（SunJCE）：系统 java 11 不支持，必须用 DevEco 自带 JBR 21
    jbr = os.path.join(deveco, "jbr")
    if os.path.isdir(jbr):
        env["JAVA_HOME"] = jbr
        env["PATH"] = os.path.join(jbr, "bin") + os.pathsep + env["PATH"]
    cmd = [hvigorw, "--mode", "module", "-p", "product=default",
           "-p", "buildMode=debug", "assembleHap", "--no-daemon"]
    print("+ " + " ".join(cmd) + "  (cwd=%s)" % build_root)
    rc = subprocess.run(cmd, cwd=build_root, env=env).returncode
    if rc != 0:
        print("ERROR: hvigor build failed (rc=%d)" % rc)
        return rc

    # 4) 回收产物（优先签名包：云调试/真机安装都要求已签名）
    outdir = os.path.join(build_root, "entry", "build", "*", "outputs", "*")
    signed = glob.glob(os.path.join(outdir, "*signed*.hap"))
    haps = glob.glob(os.path.join(outdir, "*.hap"))
    if not haps:
        print("ERROR: no hap produced (pattern %s)" % outdir)
        return 1
    src = signed[0] if signed else haps[0]
    dst = OUT_HAP_SIGNED if signed else OUT_HAP
    shutil.copy2(src, dst)
    print("hap: %s (%d bytes)%s" % (dst, os.path.getsize(dst),
                                    "" if signed else "  [UNSIGNED]"))

    if not args.keep_build:
        shutil.rmtree(build_root, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
