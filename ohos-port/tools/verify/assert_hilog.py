#!/usr/bin/env python3
# assert_hilog.py — hilog 窗口断言（B 文档 §测试基础设施约定）
#
# 用法：
#   hdc shell hilog -x | python assert_hilog.py --pid 12345 \
#       --must "probe: started" --must-not "low_memory|KillProcess"
#   python assert_hilog.py --file hilog.txt --since 12:30:00 --must "probe: report"
#
# 行为：按 pid（默认不过滤）与 --since HH:MM:SS（同日，含毫秒）截取窗口后：
#   - 每个 --must 正则至少命中 1 行；
#   - 每个 --must-not 正则命中 0 行。
# 退出码：0 = 全部满足；1 = 任一不满足（打印未命中/违例明细）。
import argparse
import re
import sys

# 标准 hilog 行：MM-DD HH:MM:SS.ms PID TID LVL domain/tag: msg（宽容解析）
HILOG_RE = re.compile(
    r"^(\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2}(?:\.\d+)?)\s+(\d+)\s+(\d+)\s+([VDIWEF])\s+(.*)$")


def parse_lines(text):
    """→ [(time_str, pid_int_or_None, line)]；不匹配行 pid=None 保留（便于调试）。"""
    out = []
    for line in text.splitlines():
        m = HILOG_RE.match(line.strip())
        if m:
            out.append((m.group(2), int(m.group(3)), line))
        else:
            out.append((None, None, line))
    return out


def check(text, pid=None, since=None, must=(), must_not=()):
    """核心过滤+断言（selftest 直接调用）。返回 (ok, msgs)。"""
    msgs = []
    lines = parse_lines(text)

    def in_window(t):
        if pid is not None and t[1] != pid:
            return False
        if since is not None:
            if t[0] is None:
                return False
            hh, mm, ss = (t[0].split(".") + ["0"])[0].split(":")
            cur = int(hh) * 3600 + int(mm) * 60 + int(ss)
            if cur < since:
                return False
        return True

    window = [t for t in lines if in_window(t)]

    if pid is not None and not window:
        msgs.append("no lines for pid=%d (window empty)" % pid)
        return False, msgs

    for pat in must:
        rx = re.compile(pat)
        hits = [t[2] for t in window if rx.search(t[2])]
        if hits:
            msgs.append("MUST hit  '%s' x%d" % (pat, len(hits)))
        else:
            msgs.append("MUST MISS '%s'" % pat)

    ok = True
    for pat in must:
        if not any(re.compile(pat).search(t[2]) for t in window):
            ok = False
    for pat in must_not:
        rx = re.compile(pat)
        hits = [t[2] for t in window if rx.search(t[2])]
        if hits:
            ok = False
            msgs.append("MUST-NOT VIOLATION '%s' x%d" % (pat, len(hits)))
        else:
            msgs.append("MUST-NOT clear '%s'" % pat)
    return ok, msgs


def _parse_since(s):
    parts = s.split(":")
    if len(parts) != 3:
        raise argparse.ArgumentTypeError("since must be HH:MM:SS")
    return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(float(parts[2]))


def selftest():
    sample = "\n".join([
        "11-05 12:00:00.100  100  200 I A0PROBE/probe: old noise",
        "11-05 12:00:05.000   1234   567 I C0F0F0/probe: probe: started",
        "11-05 12:00:05.100   1234   567 I C0F0F0/probe: check vk_surface ok",
        "11-05 12:00:06.000   1234   567 E C0F0F0/probe: FAIL futex note=blocked=0",
        "11-05 12:00:07.000   1234   567 I C0F0F0/probe: probe: report at /data/local/tmp/probe_report.json",
        "11-05 12:00:08.000  9999   888 I C0F0F0/other: low_memory killer active",
    ])
    failed = 0

    def check_(name, cond):
        nonlocal failed
        print("  [%s] %s" % ("OK" if cond else "FAIL", name))
        if not cond:
            failed += 1

    ok, msgs = check(sample, pid=1234, must=[r"probe: started"],
                     must_not=[r"low_memory"])
    check_("pid filter + must hit + must-not scoped to pid", ok and
           any("MUST hit" in m for m in msgs))
    ok, _ = check(sample, pid=1234, must=[r"probe: started", r"probe: report"])
    check_("multiple must", ok)
    ok, msgs = check(sample, pid=1234, must_not=[r"FAIL futex"])
    check_("must-not catches violation", not ok)
    ok, _ = check(sample, pid=4321, must=[r"probe: started"])
    check_("wrong pid rejected", not ok)
    ok, _ = check(sample, pid=1234, since=_parse_since("12:00:06"),
                  must=[r"probe: report"])
    check_("since window keeps later lines", ok)
    ok, _ = check(sample, pid=1234, since=_parse_since("12:00:06"),
                  must=[r"probe: started"])
    check_("since window drops earlier lines", not ok)
    ok, _ = check("garbage line\nnot hilog", must=[r"anything"])
    check_("non-hilog input fails must", not ok)
    return failed


def main():
    ap = argparse.ArgumentParser(description="hilog window assertion")
    ap.add_argument("--file", default=None, help="hilog text file (default stdin)")
    ap.add_argument("--pid", type=int, default=None)
    ap.add_argument("--since", type=_parse_since, default=None,
                    help="HH:MM:SS（同日窗口下界）")
    ap.add_argument("--must", action="append", default=[],
                    help="必须命中的正则（可多次）")
    ap.add_argument("--must-not", action="append", default=[],
                    help="必须不出现的正则（可多次）")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        sys.exit(1 if selftest() else 0)

    text = open(args.file, encoding="utf-8", errors="replace").read() \
        if args.file else sys.stdin.read()
    ok, msgs = check(text, pid=args.pid, since=args.since,
                     must=args.must, must_not=args.must_not)
    for m in msgs:
        print("  " + m)
    print("PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
