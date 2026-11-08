#!/bin/sh
# run_probe.sh — 探针真机一键执行（M0 各 TC 的载体命令）
# 用法：
#   run_probe.sh <probe.hap> [bundle] [ability] [wait_s]
#   run_probe.sh out/probe.hap ohos.probe ProbeAbility 40
# 步骤（TC0-1-2）：安装 → 清 hilog → 拉起 Ability → 等 worker 跑完 →
# 拉回 probe_report.json / probe_frames.jsonl → 落 hilog 窗口。
# 前置：hdc 在 PATH；探针已包含签名（签名流程见 docs/verify/G0.1.md）。
set -u

HAP="${1:?usage: run_probe.sh <probe.hap> [bundle] [ability] [wait_s]}"
BUNDLE="${2:-ohos.probe}"
ABILITY="${3:-ProbeAbility}"
WAIT="${4:-40}"

OUTDIR="out/device"
mkdir -p "$OUTDIR"

echo "== install $HAP =="
hdc install -r "$HAP" || { echo "FAIL: hdc install"; exit 1; }

echo "== clear hilog =="
hdc shell hilog -r >/dev/null

echo "== start ability $BUNDLE/$ABILITY =="
hdc shell aa start -b "$BUNDLE" -a "$ABILITY" || { echo "FAIL: aa start"; exit 1; }

echo "== wait ${WAIT}s for probe worker =="
sleep "$WAIT"

echo "== pull report =="
# 主路径 /data/local/tmp（shell 可读）；探针写不进时回退沙盒 files（shell 用户
# 可能读不到，此时提示走 hdc file recv + 应用沙盒路径，结论并入 TC0-1-3 留档）
if hdc shell "cat /data/local/tmp/probe_report.json" > "$OUTDIR/probe_report.json" 2>/dev/null \
   && [ -s "$OUTDIR/probe_report.json" ]; then
  echo "report: $OUTDIR/probe_report.json"
else
  hdc shell "cat /data/storage/el2/base/files/probe_report.json" \
    > "$OUTDIR/probe_report.json" 2>/dev/null || true
  if [ -s "$OUTDIR/probe_report.json" ]; then
    echo "report(sandbox): $OUTDIR/probe_report.json"
  else
    echo "WARN: report not readable on either path (记录到 TC0-1-3)"
  fi
fi

hdc shell "cat /data/local/tmp/probe_frames.jsonl" > "$OUTDIR/probe_frames.jsonl" 2>/dev/null || true

echo "== dump hilog window =="
hdc shell hilog -x > "$OUTDIR/hilog.txt" 2>/dev/null
echo "hilog: $OUTDIR/hilog.txt"
echo ""
echo "next:"
echo "  python tools/verify/assert_hilog.py --file $OUTDIR/hilog.txt --must 'probe: started'"
echo "  python tools/verify/verify_report.py $OUTDIR/probe_report.json   # TC0-2-1/3-1/4-1/5-regs"
echo "  python tools/verify/collect_frames.py --in $OUTDIR/probe_frames.jsonl"
echo "  python tools/verify/verify_pixel.py <snapshot>.jpeg center 15 220 120 --tol 16  # TC0-2-2"
echo ""
echo "notes:"
echo "  # 内存观测窗口：先设 DM_MEM_HOLD_SECS=30 环境再触发探针（须重建 HAP 或走"
echo "  # aa start 前 setenv 场景），窗口内用 readmem.sh 采样子进程 /proc/<pid>/status"
echo "  # readback 兜底：DM_DUMP_FRAMEBUFFER=/data/local/tmp/probe_frame.bin 时导出末帧，"
echo "  # PC 端 verify_pixel.py frame.bin readback 15 220 120 --width W --height H --tol 2"
echo "  # 真机单测：hdc file send out/bin/t_mini_nso /data/local/tmp/ && hdc shell chmod +x && hdc shell /data/local/tmp/t_mini_nso"
