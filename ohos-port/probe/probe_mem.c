/* probe_mem.c — M0.4 mmap 大地址预留（dmhost loader 布局前提）
 *
 * 三段检查各自独立（每段自带 5GB PROT_NONE 工作区，可各自隔离执行）：
 *   1) reserve5g   一次 5GB PROT_NONE|MAP_NORESERVE 预留（dmhost 默认布局）
 *   2) headroom    base+5GB 处【精确】衔接 +8GB（--alias-headroom 典型值）：
 *                  不加 MAP_FIXED_NOREPLACE 则 addr 仅是提示，"连续 13GB 布局"
 *                  未被验证 —— loader 依赖这段连续性（TC0-4-1）
 *   3) commit      base+1GB 处 MAP_FIXED commit 2GB + memset 0xAB，期间进程
 *                  必须存活（TC0-4-2 由 readmem.sh 在外部断言 VmRSS）
 *
 * 显式区分两个结论：mem.reserve_headroom_8g.ok = 总量拿到；headroom_contiguous
 * = 返回地址与 base 精确相邻。
 */
#include "probe_checks.h"
#include "probe_report.h"

#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

_Static_assert(sizeof(size_t) >= 8, "M0.4 需要 64 位 size_t（GB 级预留）");

#ifndef MAP_ANONYMOUS
#define MAP_ANONYMOUS MAP_ANON
#endif
/* 内核 ≥4.17；老内核 musl 可能未定义该宏（内核不支持时 mmap 返回 EINVAL） */
#ifndef MAP_FIXED_NOREPLACE
#define MAP_FIXED_NOREPLACE 0x100000
#endif

#define FIVE_GB  (5ULL << 30)
#define EIGHT_GB (8ULL << 30)
#define TWO_GB   (2ULL << 30)

static void *reserve_5g_base(char *note, size_t notecap)
{
    void *base = mmap(NULL, (size_t)FIVE_GB, PROT_NONE,
                      MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE, -1, 0);
    if (base == MAP_FAILED && note)
        snprintf(note, notecap, "5g mmap failed");
    return base;
}

/* 读 /proc/self/status 的 VmSize/VmRSS（kB），失败置 -1。
 * 云调试无 hdc，外部 readmem.sh 不可达——TC0-4-2 改由进程内自取入 note。 */
static void read_self_vm(long *vm_kb, long *rss_kb)
{
    *vm_kb = *rss_kb = -1;
    FILE *f = fopen("/proc/self/status", "r");
    if (!f)
        return;
    char line[256];
    while (fgets(line, sizeof line, f)) {
        if (sscanf(line, "VmSize: %ld kB", vm_kb) == 1)
            continue;
        if (sscanf(line, "VmRSS: %ld kB", rss_kb) == 1)
            break;
    }
    fclose(f);
}

/* TC0-4-2 观测窗口：readmem.sh 对本进程 /proc/<pid>/status 断言 VmSize/RSS，
 * 而 fork 隔离 + 结论即 munmap 会让外部永远采不到目标状态。设
 * DM_MEM_HOLD_SECS=N 时在 munmap 前保持映射 N 秒（父进程 run_isolated 60s
 * 超时会 SIGKILL 兜底，故 N 建议 ≤50）。默认 0 = 立即释放（无外部观测时
 * 不拖慢探针）。 */
static void hold_for_observer(void)
{
    const char *hold = getenv("DM_MEM_HOLD_SECS");
    if (!hold || !hold[0])
        return;
    long secs = atol(hold);
    if (secs <= 0)
        return;
    sleep((unsigned int)secs);
}

/* 1) 基础：一次 5GB PROT_NONE 预留 */
bool probe_mem_reserve5g(ProbeReport *r)
{
    char note[64] = "";
    void *base = reserve_5g_base(note, sizeof note);
    bool ok = (base != MAP_FAILED);
    if (ok)
        snprintf(note, sizeof note, "base=%p", base);
    if (base != MAP_FAILED)
        munmap(base, (size_t)FIVE_GB);
    r->mem_reserve_5g.ok = ok;
    snprintf(r->mem_reserve_5g.note, sizeof r->mem_reserve_5g.note, "%s", note);
    return ok;
}

/* 判断 [want, want+len) 在所有现有 mapping 之外（给 MAP_FIXED 兜底用） */
static bool range_free(void *want, size_t len)
{
    FILE *f = fopen("/proc/self/maps", "r");
    if (!f)
        return false;
    unsigned long long w0 = (unsigned long long)(uintptr_t)want;
    unsigned long long w1 = w0 + (unsigned long long)len, a = 0, b = 0;
    bool free_ = true;
    char line[512];
    while (fgets(line, sizeof line, f)) {
        if (sscanf(line, "%llx-%llx", &a, &b) != 2)
            continue;
        if (a < w1 && b > w0) {   /* 与目标区间有交集 */
            free_ = false;
            break;
        }
    }
    fclose(f);
    return free_;
}

/* 2) headroom：base+5GB 处精确衔接 +8GB
 * 三级降级（真机 E=38 ENOSYS：内核缺 MAP_FIXED_NOREPLACE）：
 *   a) NOREPLACE —— 首选，语义最安全
 *   b) MAP_FIXED —— 先查 /proc/self/maps 确认区间空闲再钉死
 *   c) 一次性 13GB 整体预留 —— 连续性由单次 mmap 天然保证
 */
bool probe_mem_headroom(ProbeReport *r)
{
    char note[96] = "";
    void *base = reserve_5g_base(note, sizeof note);
    if (base == MAP_FAILED)
        return false;

    void *want = (char *)base + FIVE_GB;
    const int flags = MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE;
    void *hi = mmap(want, (size_t)EIGHT_GB, PROT_NONE, flags | MAP_FIXED_NOREPLACE, -1, 0);
    int e_norpl = errno;
    const char *how = "noreplace";
    if (hi == MAP_FAILED && range_free(want, (size_t)EIGHT_GB)) {
        hi = mmap(want, (size_t)EIGHT_GB, PROT_NONE, flags | MAP_FIXED, -1, 0);
        how = "fixed";
    }
    bool reserved   = (hi != MAP_FAILED);
    bool contiguous = reserved && (hi == want);
    int e_fixed = errno;

    if (reserved) {
        long vm_kb = -1, rss_kb = -1;
        read_self_vm(&vm_kb, &rss_kb);
        snprintf(note, sizeof note, "%s contig=%d vm=%ldkB rss=%ldkB",
                 how, (int)contiguous, vm_kb, rss_kb);
        hold_for_observer();
        munmap(hi, (size_t)EIGHT_GB);
        munmap(base, (size_t)FIVE_GB);
    } else {
        /* c) 整体 13GB：一次 mmap 成功即等于拿到连续布局 */
        munmap(base, (size_t)FIVE_GB);
        void *one = mmap(NULL, (size_t)(FIVE_GB + EIGHT_GB), PROT_NONE, flags, -1, 0);
        int e_one = errno;
        if (one != MAP_FAILED) {
            long vm_kb = -1, rss_kb = -1;
            read_self_vm(&vm_kb, &rss_kb);
            snprintf(note, sizeof note, "single13g vm=%ldkB rss=%ldkB", vm_kb, rss_kb);
            reserved = contiguous = true;
            hold_for_observer();
            munmap(one, (size_t)(FIVE_GB + EIGHT_GB));
        } else {
            snprintf(note, sizeof note, "norpl E=%d fixed E=%d single13g E=%d",
                     e_norpl, e_fixed, e_one);
        }
    }

    r->mem_headroom_8g.ok    = reserved && contiguous;
    r->headroom_contiguous   = contiguous;
    snprintf(r->mem_headroom_8g.note, sizeof r->mem_headroom_8g.note, "%s", note);
    return reserved && contiguous;
}

/* 3) 触碰验证：alias 区实际 commit 2GB 后 memset，观察 RSS 与 LMK */
bool probe_mem_commit(ProbeReport *r)
{
    char note[64] = "";
    void *base = reserve_5g_base(note, sizeof note);
    if (base == MAP_FAILED)
        return false;

    void *touch = mmap((char *)base + (1ULL << 30), (size_t)TWO_GB,
                       PROT_READ | PROT_WRITE,
                       MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED, -1, 0);
    bool ok = false;
    if (touch != MAP_FAILED) {
        memset(touch, 0xAB, (size_t)TWO_GB);   /* 真实触碰全部页 → RSS≈2GB */
        /* 抽验首尾两页确为 0xAB（防 memset 被优化/页未落实） */
        volatile unsigned char *p = (volatile unsigned char *)touch;
        ok = p[0] == 0xAB &&
             p[(size_t)TWO_GB - 1] == 0xAB &&
             p[(size_t)TWO_GB / 2] == 0xAB;
        long vm_kb = -1, rss_kb = -1;
        read_self_vm(&vm_kb, &rss_kb);   /* RSS 应 ≈2GB（云调试进程内自取） */
        snprintf(note, sizeof note, "commit ok=%d vm=%ldkB rss=%ldkB",
                 (int)ok, vm_kb, rss_kb);
        if (ok)
            hold_for_observer();   /* RSS≈2GB 观测窗口（TC0-4-2，DM_MEM_HOLD_SECS） */
        munmap(touch, (size_t)TWO_GB);
    } else {
        snprintf(note, sizeof note, "commit mmap failed");
    }
    munmap(base, (size_t)FIVE_GB);

    r->mem_commit_2g.ok = ok;
    snprintf(r->mem_commit_2g.note, sizeof r->mem_commit_2g.note, "%s", note);
    return ok;
}
