#!/bin/sh
# readmem.sh — /proc/<pid>/status 内存断言（B 文档 §测试基础设施约定）
# 真机执行（hdc shell 或 on-device）。用法：
#   readmem.sh <name|pid> <vm_min_kb> <rss_min_kb> <rss_max_kb> <rounds> [interval_s]
# 例（TC0-4-1/TC0-4-2：13GB 布局 + 2GB commit）：
#   readmem.sh probe 13631488 1572864 2621440 10 1
# 每轮打印 VmSize/VmRSS；VmSize < vm_min 或 RSS 越界 → 立即 FAIL(exit 1)；
# 进程消失 → FAIL；全部轮次通过 → PASS(exit 0)。
set -u

NAME="$1"; VM_MIN="$2"; RSS_MIN="$3"; RSS_MAX="$4"; ROUNDS="$5"
INTERVAL="${6:-1}"

pidof_line() {
  # name 可能是 pid：纯数字直接用
  case "$NAME" in
    ''|*[!0-9]*) pidof "$NAME" | awk '{print $1}' ;;
    *) echo "$NAME" ;;
  esac
}

round=0
while [ "$round" -lt "$ROUNDS" ]; do
  PID=$(pidof_line)
  if [ -z "$PID" ] || [ ! -r "/proc/$PID/status" ]; then
    echo "FAIL: process $NAME not found (round $round)"
    exit 1
  fi
  VMSIZE=$(awk '/^VmSize:/{print $2}' "/proc/$PID/status")
  VMRSS=$(awk '/^VmRSS:/{print $2}' "/proc/$PID/status")
  if [ -z "$VMSIZE" ] || [ -z "$VMRSS" ]; then
    echo "FAIL: cannot read VmSize/VmRSS of pid $PID"
    exit 1
  fi
  echo "round $round pid=$PID VmSize=${VMSIZE}kB VmRSS=${VMRSS}kB"
  if [ "$VMSIZE" -lt "$VM_MIN" ]; then
    echo "FAIL: VmSize $VMSIZE < $VM_MIN (reserve layout not observed)"
    exit 1
  fi
  if [ "$VMRSS" -lt "$RSS_MIN" ] || [ "$VMRSS" -gt "$RSS_MAX" ]; then
    echo "FAIL: VmRSS $VMRSS outside [$RSS_MIN, $RSS_MAX] (commit check)"
    exit 1
  fi
  round=$((round + 1))
  sleep "$INTERVAL"
done
echo "PASS: $ROUNDS rounds OK (VmSize>=$VM_MIN, VmRSS in [$RSS_MIN,$RSS_MAX])"
exit 0
