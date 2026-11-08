#!/usr/bin/env python3
# verify_report.py — probe_report.json 判定器（G0.x 闸门读取端，无第三方依赖）
#
# 用法：
#   python verify_report.py probe_report.json                 # 全部判定
#   python verify_report.py probe_report.json --tc tc0-2-1    # 单项判定
#   python verify_report.py probe_report.json --list          # 列出全部 TC
#   python verify_report.py --selftest
#
# 判定契约（B 文档 TC 表，键名即 JSON 契约）：
#   tc0-2-1  vk.has_vk_ohos_surface == true 且 api_version_raw >= 1.1
#   tc0-3-1  bcn 恰好 16 个 VK_FORMAT_* 键（与键表逐一对应，值全为 bool）。
#            键序一致性是对 probe_json.c 产物的加严（C 侧 _Static_assert 锁序），
#            非 B 文档要求；他源工具生成的无序报告会被本判定误拒
#   tc0-3-3  bcn_all=false 时 astc 4x4/8x8 均 true（转码兜底前提）；
#            bcn_all=true 时本项 SKIP
#   tc0-4-1  mem.reserve_5g 且 reserve_headroom_8g 且 headroom_contiguous
#   tc0-5-regs  regs 七项（tpidr_rw/tpidrro_readable/cntpct_ok/tls_per_thread/
#               futex_ok/wx_ok/rewritten_nso_runs）全为 true
#
# schema 检查（始终执行）：顶层分组齐全、checks 明细 11 项齐全、bcn 键表一致。
import argparse
import json
import sys

# 与 probe/probe_report.h probe_bcn_keys[] 一致（跨文件契约；C 侧以
# _Static_assert 锁序，本表是同一契约的 Python 镜像，改动须两侧同步）
BCN_KEYS = [
    "VK_FORMAT_BC1_RGB_UNORM_BLOCK",   "VK_FORMAT_BC1_RGB_SRGB_BLOCK",
    "VK_FORMAT_BC1_RGBA_UNORM_BLOCK",  "VK_FORMAT_BC1_RGBA_SRGB_BLOCK",
    "VK_FORMAT_BC2_UNORM_BLOCK",       "VK_FORMAT_BC2_SRGB_BLOCK",
    "VK_FORMAT_BC3_UNORM_BLOCK",       "VK_FORMAT_BC3_SRGB_BLOCK",
    "VK_FORMAT_BC4_UNORM_BLOCK",       "VK_FORMAT_BC4_SNORM_BLOCK",
    "VK_FORMAT_BC5_UNORM_BLOCK",       "VK_FORMAT_BC5_SNORM_BLOCK",
    "VK_FORMAT_BC6H_UFLOAT_BLOCK",     "VK_FORMAT_BC6H_SFLOAT_BLOCK",
    "VK_FORMAT_BC7_UNORM_BLOCK",       "VK_FORMAT_BC7_SRGB_BLOCK",
]

REQUIRED_CHECKS = ["vk_surface", "mem_reserve_5g", "mem_headroom_8g",
                   "mem_commit_2g", "tpidr_rw", "tpidrro", "cntpct",
                   "tls_per_thread", "futex", "wx", "mini_nso"]

API_11_RAW = (1 << 22) | (1 << 12)   # packed VK_API_VERSION_1_1（patch=0）

REGS_ALL_TRUE = ["tpidr_rw", "tpidrro_readable", "cntpct_ok",
                 "tls_per_thread", "futex_ok", "wx_ok", "rewritten_nso_runs"]


# ---------------------------------------------------------------- schema
def check_schema(rep):
    """结构完整性：缺键/类型不符逐条列出。返回错误列表（空=通过）。"""
    errs = []
    for group in ("meta", "vk", "bcn", "astc", "mem", "regs", "checks"):
        if not isinstance(rep.get(group), dict):
            errs.append("missing group: %s" % group)
    if isinstance(rep.get("bcn"), dict):
        bcn = rep["bcn"]
        if list(bcn.keys()) != BCN_KEYS:
            errs.append("bcn keys mismatch (want 16 keys in contract order)")
        else:
            for k, v in bcn.items():
                if not isinstance(v, bool):
                    errs.append("bcn.%s not bool" % k)
    if isinstance(rep.get("vk"), dict):
        vk = rep["vk"]
        if "has_vk_ohos_surface" not in vk:
            errs.append("vk.has_vk_ohos_surface missing")
        if "api_version_raw" not in vk:
            errs.append("vk.api_version_raw missing")
    if isinstance(rep.get("checks"), dict):
        for c in REQUIRED_CHECKS:
            it = rep["checks"].get(c)
            if not isinstance(it, dict):
                errs.append("checks.%s missing" % c)
            elif not all(k in it for k in ("ok", "crashed", "sig", "note")):
                errs.append("checks.%s incomplete item" % c)
    return errs


# ---------------------------------------------------------------- TC 判定
# 每项返回 (ok, detail)；判定键缺失时 ok=False 且 detail 指明原因。
def tc0_2_1(rep):
    vk = rep.get("vk", {})
    has = vk.get("has_vk_ohos_surface")
    raw = vk.get("api_version_raw", 0)
    ok = has is True and isinstance(raw, int) and raw >= API_11_RAW
    return ok, "has_surface=%s api_raw=%s (need true & >=%d)" % (
        has, raw, API_11_RAW)


def tc0_3_1(rep):
    bcn = rep.get("bcn")
    if not isinstance(bcn, dict):
        return False, "bcn group missing"
    n = len(bcn)
    exact = list(bcn.keys()) == BCN_KEYS
    all_bool = all(isinstance(v, bool) for v in bcn.values())
    n_true = sum(1 for v in bcn.values() if v is True)
    ok = exact and all_bool
    return ok, "keys=%d(exact=%s) all_bool=%s supported=%d" % (
        n, exact, all_bool, n_true)


def tc0_3_3(rep):
    astc = rep.get("astc", {})
    if rep.get("bcn_all") is True:
        return None, "SKIP (bcn_all=true, M3.4 转码线不需要)"
    a4 = astc.get("4x4")
    a8 = astc.get("8x8")
    ok = a4 is True and a8 is True
    return ok, "astc 4x4=%s 8x8=%s (need true & true)" % (a4, a8)


def tc0_4_1(rep):
    mem = rep.get("mem", {})
    r5 = mem.get("reserve_5g")
    h8 = mem.get("reserve_headroom_8g")
    ct = mem.get("headroom_contiguous")
    ok = r5 is True and h8 is True and ct is True
    return ok, "reserve_5g=%s headroom_8g=%s contiguous=%s (need all true)" % (
        r5, h8, ct)


def tc0_5_regs(rep):
    regs = rep.get("regs", {})
    missing = [k for k in REGS_ALL_TRUE if regs.get(k) is not True]
    ok = not missing
    return ok, "all true" if ok else "not true: %s" % ", ".join(missing)


TCS = [
    ("tc0-2-1", tc0_2_1, "Vulkan + VK_OHOS_surface，API ≥ 1.1"),
    ("tc0-3-1", tc0_3_1, "BCn 恰好 16 键且逐键 bool"),
    ("tc0-3-3", tc0_3_3, "bcn_all=false 时 ASTC 可用（转码兜底）"),
    ("tc0-4-1", tc0_4_1, "mmap 5GB+8GB 连续预留"),
    ("tc0-5-regs", tc0_5_regs, "寄存器/futex/W^X/重写码 七项全过"),
]


# ---------------------------------------------------------------- selftest
def _sample_report(**over):
    """构造一份全 PASS 的样例报告；over 可覆写指定字段制造失败样本。"""
    rep = {
        "meta": {"started_at": "t0", "finished_at": "t1",
                 "device_model": "m", "os_version": "o"},
        "vk": {"has_vk_ohos_surface": True, "api_version": "1.1.0",
               "api_version_raw": API_11_RAW, "device_name": "d",
               "driver_info": "i", "present_ms": 1.0},
        "bcn": {k: True for k in BCN_KEYS},
        "bcn_all": True,
        "astc": {"4x4": True, "8x8": True},
        "mem": {"reserve_5g": True, "reserve_headroom_8g": True,
                "headroom_contiguous": True, "commit_2g_ok": True},
        "regs": {k: True for k in REGS_ALL_TRUE},
        "checks": {c: {"ok": True, "crashed": False, "sig": 0, "note": ""}
                   for c in REQUIRED_CHECKS},
    }
    for path, val in over.items():
        if "." in path:
            group, key = path.split(".", 1)
            rep[group][key] = val
        else:
            rep[path] = val
    return rep


def selftest():
    failed = 0

    def check(name, cond):
        nonlocal failed
        print("  [%s] %s" % ("OK" if cond else "FAIL", name))
        if not cond:
            failed += 1

    good = _sample_report()
    check("schema of good report", check_schema(good) == [])
    for tc, fn, _ in TCS:
        ok, _detail = fn(good)
        if tc == "tc0-3-3":
            # good 样例 bcn_all=true → tc0-3-3 按契约 SKIP（None），
            # PASS 分支用 bcn_all=false + ASTC 全真的变体单独验证
            check("tc0-3-3 skip on good report", ok is None)
            ok2, _ = tc0_3_3(_sample_report(bcn_all=False))
            check("tc0-3-3 pass with astc", ok2 is True)
        else:
            check("%s pass on good report" % tc, ok is True)

    # tc0-2-1：缺 surface / API 太老
    ok, _ = tc0_2_1(_sample_report(**{"vk.has_vk_ohos_surface": False}))
    check("tc0-2-1 reject no surface", ok is False)
    ok, _ = tc0_2_1(_sample_report(**{"vk.api_version_raw": (1 << 22)}))
    check("tc0-2-1 reject api<1.1", ok is False)

    # tc0-3-1：少一个键 / 键序错 / 非 bool
    bad_bcn = {k: True for k in BCN_KEYS[:-1]}
    ok, _ = tc0_3_1(_sample_report(**{"bcn": bad_bcn}))
    check("tc0-3-1 reject 15 keys", ok is False)
    rev = {k: True for k in reversed(BCN_KEYS)}
    ok, _ = tc0_3_1(_sample_report(**{"bcn": rev}))
    check("tc0-3-1 reject wrong order", ok is False)

    # tc0-3-3：bcn_all=false 且 ASTC 缺 → FAIL；bcn_all=true → SKIP（None）
    ok, _ = tc0_3_3(_sample_report(bcn_all=False, **{"astc.8x8": False}))
    check("tc0-3-3 reject no astc", ok is False)
    ok, _ = tc0_3_3(_sample_report(bcn_all=True))
    check("tc0-3-3 skip when bcn_all", ok is None)

    # tc0-4-1：不连续必须拒
    ok, _ = tc0_4_1(_sample_report(**{"mem.headroom_contiguous": False}))
    check("tc0-4-1 reject non-contiguous", ok is False)

    # tc0-5-regs：缺一项即拒
    ok, _ = tc0_5_regs(_sample_report(**{"regs.wx_ok": False}))
    check("tc0-5-regs reject missing", ok is False)

    # schema：坏键表 / 缺 checks 明细
    bad = _sample_report()
    bad["bcn"] = {k: True for k in BCN_KEYS[:8]}
    check("schema reject short bcn", check_schema(bad) != [])
    bad = _sample_report()
    del bad["checks"]["wx"]
    check("schema reject missing check", check_schema(bad) != [])
    return failed


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="probe_report.json TC judge")
    ap.add_argument("report", nargs="?", help="probe_report.json path")
    ap.add_argument("--tc", default=None,
                    help="single TC id (e.g. tc0-2-1); default = all")
    ap.add_argument("--list", action="store_true", help="list TC ids")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        sys.exit(1 if selftest() else 0)
    if args.list:
        for tc, _, desc in TCS:
            print("%-12s %s" % (tc, desc))
        return 0
    if not args.report:
        ap.error("report path required (or --selftest/--list)")

    with open(args.report, "r", encoding="utf-8") as f:
        rep = json.load(f)

    schema_errs = check_schema(rep)
    for e in schema_errs:
        print("SCHEMA: %s" % e)

    selected = TCS if args.tc is None else [t for t in TCS if t[0] == args.tc]
    if not selected:
        print("unknown TC: %s (--list for ids)" % args.tc)
        return 2

    failed = 0
    for tc, fn, desc in selected:
        res, detail = fn(rep)
        if res is None:
            print("[SKIP ] %-12s %s | %s" % (tc, desc, detail))
        elif res:
            print("[PASS ] %-12s %s | %s" % (tc, desc, detail))
        else:
            print("[FAIL ] %-12s %s | %s" % (tc, desc, detail))
            failed += 1
    failed += len(schema_errs)
    print("verify_report: %s" % ("ALL GREEN" if failed == 0
                                 else "%d failure(s)" % failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
