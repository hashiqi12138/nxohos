#!/usr/bin/env python3
# collect_frames.py — present 时间戳 → fps/间隔统计（B 文档 §测试基础设施约定）
#
# 输入：probe_frames.jsonl（probe_vk.c 落盘），每行：
#   {"i":0,"submit_ns":1699...,"present_ns":1699...,"ms":16.7}
# 输出 JSON：
#   {"frames":N,"fps_overall":..,"fps_mean":..,
#    "interval_ms":{"mean":..,"p50":..,"p95":..,"std":..}}
#
# 用法：
#   python collect_frames.py --in probe_frames.jsonl --out frames_report.json
#   cat probe_frames.jsonl | python collect_frames.py
import argparse
import json
import math
import sys


def pct(sorted_vals, p):
    """线性插值百分位（p ∈ [0,100]）。"""
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * p / 100.0
    f = math.floor(k)
    c = min(f + 1, len(sorted_vals) - 1)
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def analyze(records):
    """records: [{"i":..,"submit_ns":..,"present_ns":..}] → 统计 dict。"""
    pts = sorted((r["present_ns"] for r in records if "present_ns" in r))
    n = len(pts)
    if n < 2:
        return {"frames": n, "fps_overall": 0.0, "fps_mean": 0.0,
                "interval_ms": {"mean": 0.0, "p50": 0.0, "p95": 0.0, "std": 0.0}}
    intervals_ms = [(b - a) / 1e6 for a, b in zip(pts, pts[1:])]
    srt = sorted(intervals_ms)
    mean = sum(intervals_ms) / len(intervals_ms)
    std = math.sqrt(sum((v - mean) ** 2 for v in intervals_ms) / len(intervals_ms))
    return {
        "frames": n,
        # present_ns 为 CLOCK_MONOTONIC 纳秒：fps = (n-1) 个间隔 / 总时长(秒)
        "fps_overall": 1e9 * (n - 1) / (pts[-1] - pts[0])
        if pts[-1] > pts[0] else 0.0,
        "fps_mean": 1000.0 / mean if mean > 0 else 0.0,
        "interval_ms": {
            "mean": mean,
            "p50": pct(srt, 50),
            "p95": pct(srt, 95),
            "std": std,
        },
    }


def selftest():
    failed = 0

    def check(name, cond):
        nonlocal failed
        print("  [%s] %s" % ("OK" if cond else "FAIL", name))
        if not cond:
            failed += 1

    # 300 帧 @60Hz + 少量抖动
    recs = [{"i": i, "submit_ns": i * 16666667,
             "present_ns": i * 16666667 + 2000000} for i in range(300)]
    st = analyze(recs)
    check("frames", st["frames"] == 300)
    check("fps_overall≈60", abs(st["fps_overall"] - 60.0) < 0.1)
    check("fps_mean≈60", abs(st["fps_mean"] - 60.0) < 0.1)
    check("p50≈16.67", abs(st["interval_ms"]["p50"] - 16.667) < 0.01)
    check("std≈0", st["interval_ms"]["std"] < 0.001)

    # 乱序/缺字段容错
    mixed = [recs[5], {"i": 99}, recs[1], recs[3]]
    st2 = analyze(mixed)
    check("partial input frames", st2["frames"] == 3)

    # 单帧/空输入不崩
    check("single frame", analyze([recs[0]])["frames"] == 1)
    check("empty", analyze([])["frames"] == 0)
    return failed


def main():
    ap = argparse.ArgumentParser(description="present timestamp statistics")
    ap.add_argument("--in", dest="infile", default=None,
                    help="probe_frames.jsonl（缺省读 stdin）")
    ap.add_argument("--out", default=None, help="统计 JSON 输出路径（缺省 stdout）")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        sys.exit(1 if selftest() else 0)

    text = open(args.infile, encoding="utf-8") if args.infile else sys.stdin
    records = []
    for line in text:
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            print("WARN: skip bad line: %s" % line[:60], file=sys.stderr)

    if hasattr(text, "close"):
        text.close()

    report = analyze(records)
    out = json.dumps(report, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(out + "\n")
        print("wrote %s" % args.out)
    print(out)
    sys.exit(0 if report["frames"] >= 2 else 1)


if __name__ == "__main__":
    main()
