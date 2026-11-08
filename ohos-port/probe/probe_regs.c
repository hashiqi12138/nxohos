/* probe_regs.c — M0.5 寄存器 / futex / 代码页语义（重写器前置条件，最高优先级）
 *
 * 七项检查（B 文档 §M0.5，逐条验证 patch_nso.py 依赖的语义）：
 *   a) tpidr_el0 可写性（哨兵写读恢复，防恒真；msr↔恢复之间不得访问任何 TLS）
 *   b) tpidrro_el0 EL0 可读（note 记录 zero/nonzero）
 *   c) cntpct_el0 可读且单调（按 cntfrq_el0 实际频率换算 + 10% 容差，宿主时钟计时）
 *   d) 每线程 tpidr_el0 隔离（KTLS 槽前提）
 *   e) futex 等待/唤醒语义（地址仲裁器前提）
 *   f) W^X：RW 页 mprotect 成 RX 后可执行（主报告风险5 的验证落点）
 *   g) 重写码实测（mini_host.c 装载重写形态迷你 NSO）
 *
 * 每项都在 probe_main.c 的 fork 隔离子进程中运行：本文件内的任何 trap（如
 * tpidrro 读 SIGSEGV）只影响该项结论；子进程内不使用 hilog/stdio。
 */
#include "probe_checks.h"
#include "probe_report.h"
#include "mini_host.h"

#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <sys/mman.h>
#include <dlfcn.h>
#include <link.h>
#include <sys/stat.h>
#include <sys/syscall.h>

#if defined(__aarch64__) && !defined(DM_OHOS_HOST_TEST)
#define PROBE_HAVE_ARM64 1
#endif

/* ---------- M0.5 a) tpidr_el0 可写性 ---------- */
#ifdef PROBE_HAVE_ARM64
static inline uint64_t read_tpidr(void)
{
    uint64_t v;
    asm volatile("mrs %0, tpidr_el0" : "=r"(v));
    return v;
}
#endif

bool probe_tpidr_rw(ProbeReport *r)
{
#ifndef PROBE_HAVE_ARM64
    snprintf(r->tpidr_rw.note, sizeof r->tpidr_rw.note, "host stub");
    return false;
#else
    uint64_t t0, t1;
    asm volatile("mrs %0, tpidr_el0" : "=r"(t0));
    uint64_t sentinel = probe_sentinel(t0);       /* 非自身哨兵值 */
    /* —— 以下 msr 与恢复之间不得访问任何 TLS（仅寄存器运算）—— */
    asm volatile("msr tpidr_el0, %0" :: "r"(sentinel));
    asm volatile("mrs %0, tpidr_el0" : "=r"(t1));
    asm volatile("msr tpidr_el0, %0" :: "r"(t0)); /* 立即恢复原值 */
    /* —— 恢复之后才允许触碰 TLS/调用函数 —— */
    bool writable = probe_tpidr_writable_check(t0, t1);
    r->tpidr_rw.ok = writable;
    snprintf(r->tpidr_rw.note, sizeof r->tpidr_rw.note, "t0=%016llx",
             (unsigned long long)t0);
    return writable;
#endif
}

/* ---------- M0.5 b) tpidrro_el0 EL0 可读 ---------- */
bool probe_tpidrro(ProbeReport *r)
{
#ifndef PROBE_HAVE_ARM64
    snprintf(r->tpidrro.note, sizeof r->tpidrro.note, "host stub");
    return false;
#else
    uint64_t ro;
    asm volatile("mrs %0, tpidrro_el0" : "=r"(ro));
    r->tpidrro.ok = true;                        /* 走到这里即未被内核 SIGSEGV */
    snprintf(r->tpidrro.note, sizeof r->tpidrro.note, "%s",
             ro == 0 ? "zero" : "nonzero");
    return true;
#endif
}

/* ---------- M0.5 c) cntpct_el0 可读且单调 ---------- */
static uint64_t mono_ns(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ULL + (uint64_t)ts.tv_nsec;
}

bool probe_cntpct(ProbeReport *r)
{
#ifndef PROBE_HAVE_ARM64
    snprintf(r->cntpct.note, sizeof r->cntpct.note, "host stub");
    return false;
#else
    uint64_t freq, a, b;
    asm volatile("mrs %0, cntfrq_el0" : "=r"(freq));  /* 先读实际定时器频率 */
    r->cntfrq_hz = freq;
    asm volatile("mrs %0, cntpct_el0" : "=r"(a));
    uint64_t t0 = mono_ns();                          /* 宿主时钟计时（不用 cntpct 自校准） */
    volatile uint64_t sink = 0;                       /* 自旋 10ms（时钟驱动跳出） */
    while (mono_ns() - t0 < 10ULL * 1000000ULL)
        sink++;
    uint64_t elapsed_ms = (mono_ns() - t0) / 1000000ULL;
    asm volatile("mrs %0, cntpct_el0" : "=r"(b));
    (void)sink;
    bool ok = probe_cntpct_judge(freq, a, b, elapsed_ms);
    r->cntpct.ok = ok;
    snprintf(r->cntpct.note, sizeof r->cntpct.note, "ms=%llu",
             (unsigned long long)elapsed_ms);
    return ok;
#endif
}

/* ---------- M0.5 d) 每线程 tpidr_el0 隔离 ---------- */
#include <pthread.h>

static uint64_t g_tls_self, g_tls_other;

static void *tls_thread_fn(void *arg)
{
    (void)arg;
#ifdef PROBE_HAVE_ARM64
    g_tls_other = read_tpidr();
#endif
    return NULL;
}

bool probe_tls_per_thread(ProbeReport *r)
{
#ifndef PROBE_HAVE_ARM64
    snprintf(r->tls_per_thread.note, sizeof r->tls_per_thread.note, "host stub");
    return false;
#else
    g_tls_self  = read_tpidr();
    pthread_t th;
    int rc = pthread_create(&th, NULL, tls_thread_fn, NULL);
    if (rc != 0) {
        snprintf(r->tls_per_thread.note, sizeof r->tls_per_thread.note,
                 "pthread_create rc=%d", rc);
        return false;
    }
    pthread_join(th, NULL);
    bool ok = (g_tls_self != 0) && (g_tls_self != g_tls_other);
    r->tls_per_thread.ok = ok;
    snprintf(r->tls_per_thread.note, sizeof r->tls_per_thread.note,
             "self=%llx other=%llx", (unsigned long long)g_tls_self,
             (unsigned long long)g_tls_other);
    return ok;
#endif
}

/* ---------- M0.5 e) futex 等待/唤醒（地址仲裁器前提） ---------- */
/* 不引 <linux/futex.h>（OHOS sysroot 未必带）：FUTEX_WAIT/WAKE 值是内核 ABI 常量 */
#define DM_FUTEX_WAIT 0
#define DM_FUTEX_WAKE 1

static inline long sys_futex(int *uaddr, int op, int val, const struct timespec *timeout)
{
    return syscall(SYS_futex, uaddr, op, val, timeout, NULL, 0);
}

static int                g_fut;
static volatile int       g_waiter_entered;
static volatile int       g_waiter_done;
static volatile int       g_waiter_spurious;
static volatile long long g_waiter_elapsed_ms;

static void *futex_waiter_fn(void *arg)
{
    (void)arg;
    g_waiter_entered = 1;
    uint64_t t0 = (uint64_t)mono_ns();
    struct timespec deadline = { .tv_sec = 0, .tv_nsec = 500 * 1000 * 1000 };
    for (;;) {
        /* 期望值 0：宿主置 1 唤醒；EAGAIN=值已变（立即成功）；EINTR 记为伪唤醒并重试 */
        long rc = sys_futex(&g_fut, DM_FUTEX_WAIT, 0, &deadline);
        if (rc == 0) {
            break;                                   /* 被正常唤醒 */
        }
        if (rc == -1 && errno == EINTR) {
            g_waiter_spurious++;
            continue;
        }
        if (rc == -1 && errno == EAGAIN) {
            break;                                   /* 唤醒先于阻塞：视为即时成功 */
        }
        break;                                       /* ETIMEDOUT / 其他：失败退出 */
    }
    g_waiter_elapsed_ms = (long long)((uint64_t)mono_ns() - t0) / 1000000LL;
    g_waiter_done = 1;
    return NULL;
}

bool probe_futex(ProbeReport *r)
{
    g_fut = 0;
    g_waiter_entered = g_waiter_done = g_waiter_spurious = 0;
    g_waiter_elapsed_ms = -1;

    pthread_t th;
    int rc = pthread_create(&th, NULL, futex_waiter_fn, NULL);
    if (rc != 0) {
        snprintf(r->futex.note, sizeof r->futex.note, "pthread_create rc=%d", rc);
        return false;
    }
    /* 等 waiter 进入阻塞（上限 100ms 轮询）；随后断言它仍被阻塞（未被提前唤醒） */
    for (int i = 0; i < 100 && !g_waiter_entered; i++) {
        struct timespec ms1 = { .tv_sec = 0, .tv_nsec = 1000 * 1000 };
        nanosleep(&ms1, NULL);
    }
    struct timespec ms100 = { .tv_sec = 0, .tv_nsec = 100 * 1000 * 1000 };
    nanosleep(&ms100, NULL);
    bool still_blocked = g_waiter_entered && !g_waiter_done;

    __atomic_store_n(&g_fut, 1, __ATOMIC_SEQ_CST);
    long woke = sys_futex(&g_fut, DM_FUTEX_WAKE, 1, NULL);
    pthread_join(th, NULL);

    bool ok = still_blocked && g_waiter_done && woke >= 1 &&
              g_waiter_spurious <= 3 && g_waiter_elapsed_ms < 500;
    r->futex.ok = ok;
    snprintf(r->futex.note, sizeof r->futex.note,
             "blocked=%d woke=%ld spur=%d %lldms", (int)still_blocked, woke,
             g_waiter_spurious, g_waiter_elapsed_ms);
    return ok;
}

/* ---------- M0.5 f) W^X：RW→RX 代码页可执行 ----------
 * 真机 SELinux 拒绝了匿名页的 mprotect(W|X 转换)，故按下面三级降级实测，
 * 结果决定 M1 的代码重写落地方式：
 *   a) rw2rx  匿名 RW → mprotect RX（标准做法）
 *   b) rwx    匿名页直接带 PROT_EXEC 映射（execmem）
 *   c) file   应用沙盒内临时文件双映射（一段 RW、一段 RX，MAP_SHARED）
 */
static int wx_rw2rx(const unsigned char *bin, size_t len, char *note, size_t notecap)
{
    void *p = mmap(NULL, len, PROT_READ | PROT_WRITE,
                   MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (p == MAP_FAILED) {
        snprintf(note, notecap, "mmap E=%d", errno);
        return -1;
    }
    memcpy(p, bin, len);
    __builtin___clear_cache((char *)p, (char *)p + len);
    if (mprotect(p, len, PROT_READ | PROT_EXEC) != 0) {
        snprintf(note, notecap, "mprotect E=%d", errno);
        munmap(p, len);
        return -1;
    }
    int v = ((int (*)(void))p)();
    munmap(p, len);
    snprintf(note, notecap, "call=%d", v);
    return v;
}

static int wx_rwx(const unsigned char *bin, size_t len, char *note, size_t notecap)
{
    void *p = mmap(NULL, len, PROT_READ | PROT_WRITE | PROT_EXEC,
                   MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (p == MAP_FAILED) {
        snprintf(note, notecap, "rwx mmap E=%d", errno);
        return -1;
    }
    memcpy(p, bin, len);
    __builtin___clear_cache((char *)p, (char *)p + len);
    int v = ((int (*)(void))p)();
    munmap(p, len);
    snprintf(note, notecap, "rwx call=%d", v);
    return v;
}

static int wx_file(const unsigned char *bin, size_t len, char *note, size_t notecap)
{
    const char *path = "/data/storage/el2/base/files/.dmjit_probe";
    int fd = open(path, O_RDWR | O_CREAT | O_TRUNC, 0600);
    if (fd < 0) {
        snprintf(note, notecap, "open E=%d", errno);
        return -1;
    }
    if (ftruncate(fd, (off_t)len) != 0) {
        snprintf(note, notecap, "ftruncate E=%d", errno);
        close(fd);
        unlink(path);
        return -1;
    }
    void *rw = mmap(NULL, len, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (rw == MAP_FAILED) {
        snprintf(note, notecap, "map rw E=%d", errno);
        close(fd);
        unlink(path);
        return -1;
    }
    void *rx = mmap(NULL, len, PROT_READ | PROT_EXEC, MAP_SHARED, fd, 0);
    if (rx == MAP_FAILED) {
        snprintf(note, notecap, "map rx E=%d", errno);
        munmap(rw, len);
        close(fd);
        unlink(path);
        return -1;
    }
    memcpy(rw, bin, len);
    msync(rw, len, MS_SYNC);
    __builtin___clear_cache((char *)rx, (char *)rx + len);
    int v = ((int (*)(void))rx)();
    munmap(rx, len);
    munmap(rw, len);
    close(fd);
    unlink(path);
    snprintf(note, notecap, "file call=%d", v);
    return v;
}

/* d) sofile：把真实 .so 的可执行段按 MAP_PRIVATE 映射进来 → mprotect 成 RW
 *   改写 → 再 mprotect 回 RX 执行。这正是 patch_nso 的落地形态：改的是
 *   文件映射的代码页，不是匿名页，走的 SELinux 规则不同。 */
struct so_seg {
    const char *path;
    size_t      off;
};

static int find_exec_seg(struct dl_phdr_info *info, size_t size, void *data)
{
    (void)size;
    struct so_seg *s = (struct so_seg *)data;
    if (!info->dlpi_name || !info->dlpi_name[0])
        return 0;
    long pg = sysconf(_SC_PAGESIZE);
    for (int i = 0; i < info->dlpi_phnum; i++) {
        const ElfW(Phdr) *ph = &info->dlpi_phdr[i];
        if (ph->p_type == PT_LOAD && (ph->p_flags & PF_X)) {
            s->path = info->dlpi_name;
            s->off  = (size_t)ph->p_vaddr & ~(size_t)(pg - 1);
            return 1;
        }
    }
    return 0;
}

static int wx_sofile(const unsigned char *bin, size_t len, char *note, size_t notecap)
{
    struct so_seg s = { NULL, 0 };
    dl_iterate_phdr(find_exec_seg, &s);
    if (!s.path) {
        snprintf(note, notecap, "no so");
        return -1;
    }
    int fd = open(s.path, O_RDONLY);
    if (fd < 0) {
        snprintf(note, notecap, "so open E=%d", errno);
        return -1;
    }
    long pg = sysconf(_SC_PAGESIZE);
    size_t maplen = (len + (size_t)pg - 1) & ~((size_t)pg - 1);
    void *p = mmap(NULL, maplen, PROT_READ | PROT_EXEC, MAP_PRIVATE, fd,
                   (off_t)s.off);
    close(fd);
    if (p == MAP_FAILED) {
        snprintf(note, notecap, "so map E=%d", errno);
        return -1;
    }
    if (mprotect(p, maplen, PROT_READ | PROT_WRITE) != 0) {
        snprintf(note, notecap, "so mprot RW E=%d", errno);
        munmap(p, maplen);
        return -1;
    }
    memcpy(p, bin, len);
    if (mprotect(p, maplen, PROT_READ | PROT_EXEC) != 0) {
        snprintf(note, notecap, "so mprot RX E=%d", errno);
        munmap(p, maplen);
        return -1;
    }
    int v = ((int (*)(void))p)();
    munmap(p, maplen);
    snprintf(note, notecap, "so file call=%d", v);
    return v;
}

/* e) textmod：把【已加载】本库的可执行页 mprotect 成 RW → 改写 → 改回 RX。
 *    与 a/b 的区别是页面来源为文件映射（libprobe.so），走的是 execmod 规则；
 *    若这条通过，运行时直接改已加载代码即可，无需新映射。 */
struct self_seg {
    uintptr_t addr;   /* 段在进程内的起始地址（dlpi_addr + p_vaddr，页对齐后） */
    bool      found;
};

static int find_self_seg(struct dl_phdr_info *info, size_t size, void *data)
{
    (void)size;
    struct self_seg *s = (struct self_seg *)data;
    if (!info->dlpi_name || !strstr(info->dlpi_name, "libprobe.so"))
        return 0;
    long pg = sysconf(_SC_PAGESIZE);
    for (int i = 0; i < info->dlpi_phnum; i++) {
        const ElfW(Phdr) *ph = &info->dlpi_phdr[i];
        if (ph->p_type == PT_LOAD && (ph->p_flags & PF_X)) {
            s->addr  = (uintptr_t)(info->dlpi_addr + ph->p_vaddr) &
                       ~(uintptr_t)(pg - 1);
            s->found = true;
            return 1;
        }
    }
    return 0;
}

static int wx_textmod(const unsigned char *bin, size_t len, char *note, size_t notecap)
{
    (void)bin; (void)len;
    struct self_seg s = { 0, false };
    dl_iterate_phdr(find_self_seg, &s);
    if (!s.found) {
        snprintf(note, notecap, "no self seg");
        return -1;
    }
    long pg = sysconf(_SC_PAGESIZE);
    void *p = (void *)s.addr;
    if (mprotect(p, (size_t)pg, PROT_READ | PROT_WRITE) != 0) {
        snprintf(note, notecap, "text mprot RW E=%d", errno);
        return -1;
    }
    /* 写回原值（触发 COW 脏页），避免破坏正在执行的代码 */
    volatile unsigned char *q = (volatile unsigned char *)p;
    unsigned char saved = q[0];
    q[0] = saved;
    if (mprotect(p, (size_t)pg, PROT_READ | PROT_EXEC) != 0) {
        snprintf(note, notecap, "text mprot RX E=%d", errno);
        return -1;
    }
    snprintf(note, notecap, "text ok");
    return 0;
}

/* f) priv：沙盒文件按 MAP_PRIVATE|PROT_EXEC 映射（dlopen 走的正是这条），
 *    与 c) 的 MAP_SHARED 别名区分开 —— 决定"生成 .so 落地后加载"是否可行。 */
static int wx_priv(const unsigned char *bin, size_t len, char *note, size_t notecap)
{
    const char *path = "/data/storage/el2/base/files/.dmjit_priv";
    int fd = open(path, O_RDWR | O_CREAT | O_TRUNC, 0600);
    if (fd < 0) {
        snprintf(note, notecap, "priv open E=%d", errno);
        return -1;
    }
    if (write(fd, bin, len) != (ssize_t)len) {
        snprintf(note, notecap, "priv write E=%d", errno);
        close(fd);
        unlink(path);
        return -1;
    }
    long pg = sysconf(_SC_PAGESIZE);
    size_t maplen = (len + (size_t)pg - 1) & ~((size_t)pg - 1);
    void *p = mmap(NULL, maplen, PROT_READ | PROT_EXEC, MAP_PRIVATE, fd, 0);
    if (p == MAP_FAILED) {
        snprintf(note, notecap, "priv map E=%d", errno);
        close(fd);
        unlink(path);
        return -1;
    }
    int v = ((int (*)(void))p)();
    munmap(p, maplen);
    close(fd);
    unlink(path);
    snprintf(note, notecap, "priv call=%d", v);
    return v;
}

bool probe_wx(ProbeReport *r)
{
    /* "mov w0,#0; ret"（aarch64，8B，后补零至 16B）；PC x86_64 备用 "xor eax,eax; ret" */
#if defined(__aarch64__)
    static const unsigned char ret_zero_fn_bin[16] = {
        0x00, 0x00, 0x80, 0x52, 0xC0, 0x03, 0x5F, 0xD6,
        0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00
    };
#elif defined(__x86_64__)
    static const unsigned char ret_zero_fn_bin[16] = {
        0x31, 0xC0, 0xC3, 0x00, 0x00, 0x00, 0x00, 0x00,
        0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00
    };
#else
    snprintf(r->wx.note, sizeof r->wx.note, "host stub");
    return false;
#endif
    size_t len = sizeof ret_zero_fn_bin;
    char a[40] = "", b[40] = "", c[40] = "", e[40] = "", t[40] = "", f[40] = "";
    int va = wx_rw2rx(ret_zero_fn_bin, len, a, sizeof a);
    int vb = wx_rwx(ret_zero_fn_bin, len, b, sizeof b);
    int vc = wx_file(ret_zero_fn_bin, len, c, sizeof c);
    int ve = wx_sofile(ret_zero_fn_bin, len, e, sizeof e);
    int vt = wx_textmod(ret_zero_fn_bin, len, t, sizeof t);
    int vf = wx_priv(ret_zero_fn_bin, len, f, sizeof f);
    int v = (va == 0) ? va : (vb == 0) ? vb : (vc == 0) ? vc :
            (ve == 0) ? ve : (vt == 0) ? vt : (vf == 0) ? vf : -1;
    r->wx.ok = (v == 0);
    snprintf(r->wx.note, sizeof r->wx.note,
             "rw2rx[%s] rwx[%s] file[%s] so[%s] txt[%s] priv[%s]",
             a, b, c, e, t, f);
    return v == 0;
}

/* ---------- M0.5 g) 重写码实测 ---------- */
#if __has_include("mini_nso_blob.h")
#include "mini_nso_blob.h"
#include "mini_host.h"
#define HAVE_MINI_NSO_BLOB 1
#endif

bool probe_mini_nso(ProbeReport *r)
{
#ifndef HAVE_MINI_NSO_BLOB
    snprintf(r->mini_nso.note, sizeof r->mini_nso.note, "blob not generated");
    return false;
#else
    MiniGuest g = mini_guest_load(mini_nso_code, mini_nso_code_len);
    if (!g) {
        snprintf(r->mini_nso.note, sizeof r->mini_nso.note, "guest load failed");
        return false;
    }
    MiniCtx ctx = { .gate_entry = mini_gate_dispatch,
                    .slot_x18   = MINI_SLOT_X18_VALUE };
    /* 宿主 x18 金丝雀：写值 → guest 调用 → 比对（需 -ffixed-x18 编译本文件） */
    uint64_t canary = 0xC0DEBABEC0DEBABEULL;
    mini_host_x18_write(canary);

    bool gate_ok  = mini_guest_call(g, MINI_OFF_SVC, &ctx) == MINI_SVC_RESULT;
    bool x18_ok   = mini_guest_call(g, MINI_OFF_X18, &ctx) == MINI_SLOT_X18_VALUE;
    bool tls_ok   = mini_guest_call(g, MINI_OFF_TLS, &ctx) != 0;
    uint64_t ta = mini_guest_call(g, MINI_OFF_TIMER, &ctx);
    uint64_t tb = mini_guest_call(g, MINI_OFF_TIMER, &ctx);
    bool timer_ok = tb > ta;
    bool host_x18_ok = (mini_host_x18_read() == canary);

    mini_guest_free(g);
    bool ok = gate_ok && x18_ok && tls_ok && timer_ok && host_x18_ok;
    r->mini_nso.ok = ok;
    snprintf(r->mini_nso.note, sizeof r->mini_nso.note,
             "gate=%d x18=%d tls=%d timer=%d hx18=%d", (int)gate_ok,
             (int)x18_ok, (int)tls_ok, (int)timer_ok, (int)host_x18_ok);
    return ok;
#endif
}

/* ---------- M0.5 h) AOT 形态：重写码作为打包 .so 的正文 ----------
 * 真机上运行时申请可执行页的六条路径全部被拒（见 wx 的 note），因此重写器
 * 只能走 AOT：离线重写 → 产出真实 .so → 随 HAP 打包 → 系统加载器装载。
 * 本项把 mini NSO 的机码（mini_nso.S）编进独立 libmininso.so 随 hap 打包：
 * 它由加载器按正常 .so 映射成 RX，不经过任何运行时 exec 申请；dlopen 后
 * 调用四个入口，验证重写语义（gate 分派 / x18 槽 / TLS / 定时器）成立。 */
#if defined(__aarch64__) && !defined(DM_OHOS_HOST_TEST)

/* dlopen/dlsym 会在动态链接器的内部锁上阻塞；本项跑在 fork 出的子进程里，
 * 而父进程是多线程的 —— 子进程里 dlopen 会与 fork 瞬间持有锁的线程互等死锁
 * （真机实测：aot_nso 超时 60s，sig=0）。所以加载与取符号都放到父进程
 * （probe_aot_preload，fork 之前调用），子进程只做调用。 */
static uint64_t (*g_aot_svc)(MiniCtx *);
static uint64_t (*g_aot_x18)(MiniCtx *);
static uint64_t (*g_aot_tls)(MiniCtx *);
static uint64_t (*g_aot_timer)(MiniCtx *);
static char     g_aot_err[96];

void probe_aot_preload(void)
{
    void *h = dlopen("libmininso.so", RTLD_NOW);
    if (!h) {
        const char *e = dlerror();
        snprintf(g_aot_err, sizeof g_aot_err, "dlopen: %s", e ? e : "?");
        return;
    }
    g_aot_svc   = (uint64_t (*)(MiniCtx *))dlsym(h, "mini_svc_fn");
    g_aot_x18   = (uint64_t (*)(MiniCtx *))dlsym(h, "mini_x18_fn");
    g_aot_tls   = (uint64_t (*)(MiniCtx *))dlsym(h, "mini_tls_fn");
    g_aot_timer = (uint64_t (*)(MiniCtx *))dlsym(h, "mini_timer_fn");
    if (!g_aot_svc || !g_aot_x18 || !g_aot_tls || !g_aot_timer)
        snprintf(g_aot_err, sizeof g_aot_err, "dlsym missing");
}

bool probe_aot_nso(ProbeReport *r)
{
    /* libmininso.so 随 hap 打包，由系统加载器按普通 .so 映射成 RX —— 这正是
     * 重写器的 AOT 落地形态；这里只做调用，不申请任何可执行页。 */
    if (!g_aot_svc || !g_aot_x18 || !g_aot_tls || !g_aot_timer) {
        snprintf(r->aot_nso.note, sizeof r->aot_nso.note, "%s",
                 g_aot_err[0] ? g_aot_err : "not preloaded");
        return false;
    }
    uint64_t (*svc)(MiniCtx *)   = g_aot_svc;
    uint64_t (*x18)(MiniCtx *)   = g_aot_x18;
    uint64_t (*tlsf)(MiniCtx *)  = g_aot_tls;
    uint64_t (*timer)(MiniCtx *) = g_aot_timer;

    MiniCtx ctx = { .gate_entry = mini_gate_dispatch,
                    .slot_x18   = MINI_SLOT_X18_VALUE };
    /* 只读比对，绝不写宿主 x18：aarch64 上 x18 可能被用作影子调用栈指针
     * （SCS），宿主线程改写它会在下一次函数返回时踩坏返回地址 —— 那是整进程
     * 崩溃，不是本项的语义测试目标。这里只验证"guest 调用前后宿主 x18 不变"。 */
    uint64_t x18_before = mini_host_x18_read();

    bool gate_ok  = svc(&ctx) == MINI_SVC_RESULT;
    bool x18_ok   = x18(&ctx) == MINI_SLOT_X18_VALUE;
    bool tls_ok   = tlsf(&ctx) != 0;
    uint64_t ta = timer(&ctx);
    uint64_t tb = timer(&ctx);
    bool timer_ok = tb > ta;
    bool host_x18_ok = (mini_host_x18_read() == x18_before);

    bool ok = gate_ok && x18_ok && tls_ok && timer_ok && host_x18_ok;
    r->aot_nso.ok = ok;
    snprintf(r->aot_nso.note, sizeof r->aot_nso.note,
             "dlopen gate=%d x18=%d tls=%d timer=%d hx18=%d", (int)gate_ok,
             (int)x18_ok, (int)tls_ok, (int)timer_ok, (int)host_x18_ok);
    return ok;
}
#else
bool probe_aot_nso(ProbeReport *r)
{
    snprintf(r->aot_nso.note, sizeof r->aot_nso.note, "host stub");
    return false;
}
#endif
