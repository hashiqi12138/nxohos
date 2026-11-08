#!/usr/bin/env python3
# reassemble_hilog_json.py — 从云调试导出的 hilog 重组 probe_report.json
#
# 背景：AGC 云调试无 hdc，/data/local/tmp/probe_report.json 取不回；
# 探针把完整 JSON 经 hilog 分块输出（probe_main.c hilog_dump_json，无条件
# 启用），格式：
#   probe: J|<seq>|<total>|<chunk>
#   probe: J|END|<total> (len=<len>)
# 本脚本按 seq 拼接 chunk，校验 total/END 一致后写出 JSON。
#
# 用法：
#   python reassemble_hilog_json.py hilog.txt --out out/device/probe_report.json
#   python reassemble_hilog_json.py --selftest
import argparse
import json
import re
import sys

LINE_RE = re.compile(r"probe:\s*J\|(\d+)\|(\d+)\|(.*)$")
END_RE = re.compile(r"probe:\s*J\|END\|(\d+)")


def reassemble(text):
    """从 hilog 文本重组 JSON 字符串。返回 (json_str, errors)。
    errors 非空即失败原因（乱序/缺块/END 不一致等）。"""
    errors = []
    chunks = {}
    total = None
    end_total = None
    for raw in text.splitlines():
        line = raw.rstrip("\r\n")
        m = LINE_RE.search(line)
        if m:
            seq, tot, chunk = int(m.group(1)), int(m.group(2)), m.group(3)
            if total is None:
                total = tot
            elif total != tot:
                errors.append("inconsistent total %d vs %d" % (tot, total))
            total = tot
            if seq in chunks:
                errors.append("duplicate seq %d" % seq)
            chunks[seq] = chunk
            continue
        m = END_RE.search(line)
        if m:
            end_total = int(m.group(1))
    if not chunks:
        return None, ["no J| lines found (日志未导出完整或非探针日志)"]
    if total is None:
        return None, ["no total"]
    if end_total is not None and end_total != total:
        errors.append("END total %d != %d" % (end_total, total))
    missing = [str(i) for i in range(total) if i not in chunks]
    if missing:
        return None, ["missing chunks: %s" % ",".join(missing)]
    js = "".join(chunks[i] for i in range(total))
    return js, errors


def selftest():
    ok = True

    def check(name, cond):
        nonlocal ok
        print("  [%s] %s" % ("OK" if cond else "FAIL", name))
        if not cond:
            ok = False

    js = json.dumps({"meta": {"a": 1}, "bcn": {
        "VK_FORMAT_BC1_RGB_UNORM_BLOCK": True}}, ensure_ascii=False)
    # 按 20 字符切块，模拟 hilog 行（带时间戳/pid 前缀）
    chunks = [js[i:i + 20] for i in range(0, len(js), 20)]
    total = len(chunks)
    lines = []
    for i, c in enumerate(chunks):
        lines.append("11-06 10:00:00.000  12345  12345  I E0F0/probe: "
                     "J|%d|%d|%s" % (i, total, c))
    lines.append("11-06 10:00:00.001  12345  12345  I E0F0/probe: "
                 "J|END|%d (len=%d)" % (total, len(js)))

    out, errs = reassemble("\n".join(lines))
    check("reassemble roundtrip", errs == [] and out == js)
    check("out is valid json", json.loads(out) == json.loads(js))

    # 丢一块 → 报缺失
    out, errs = reassemble("\n".join(lines[:-3] + lines[-2:]))
    check("missing chunk detected", out is None and any("missing" in e for e in errs))

    # END 不一致 → 告警但重组成功
    bad = lines[:-1] + ["... probe: J|END|99 (len=1)"]
    out, errs = reassemble("\n".join(bad))
    check("end mismatch detected", out == js and any("END" in e for e in errs))

    # 无 J 行 → 明确失败
    out, errs = reassemble("random log line\nanother line")
    check("no lines fails clearly", out is None and errs and "no J| lines" in errs[0])

    # 无关行混入不干扰
    mixed = []
    for i, l in enumerate(lines):
        mixed.append("noise %d" % i)
        mixed.append(l)
    out, errs = reassemble("\n".join(mixed))
    check("noise tolerant", errs == [] and out == js)
    return ok


def main():
    ap = argparse.ArgumentParser(description="reassemble probe JSON from hilog")
    ap.add_argument("hilog", nargs="?", help="导出的 hilog 文本路径")
    ap.add_argument("--out", default="probe_report.json",
                    help="重组结果输出路径（默认 ./probe_report.json）")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        sys.exit(0 if selftest() else 1)   # selftest 返回 True=全绿
    if not args.hilog:
        ap.error("hilog path required (or --selftest)")
    with open(args.hilog, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    js, errs = reassemble(text)
    if js is None:
        for e in errs:
            print("ERROR: %s" % e)
        return 1
    try:
        json.loads(js)
    except ValueError as e:
        print("ERROR: reassembled JSON invalid: %s" % e)
        return 1
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(js)
    print("reassembled %d chars -> %s" % (len(js), args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
