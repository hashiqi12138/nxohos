// cpuprobe: checks, on a real Android device, the CPU/kernel assumptions nxnative's NSO rewriter and host make on Apple.
// Each test runs in a forked child so a fault (SIGILL, SIGBUS, SIGSEGV) is reported instead of killing the run.
// Build: aarch64-linux-android29-clang -O1 -ffixed-x18 -static cpuprobe.c -o cpuprobe -lpthread
// Run:   adb push cpuprobe /data/local/tmp/ && adb shell /data/local/tmp/cpuprobe
#define _GNU_SOURCE
#include <errno.h>
#include <pthread.h>
#include <sched.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <sys/time.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#define MAGIC 0x5678123400ad00efULL

static const char *g_detail;          // set by a test before it may fault, printed by the parent on a signal
static char g_out[512];               // a child's result line, sent to the parent through a pipe

// ---- x18: Apple's kernel zeroes it on context switch, so the rewriter moves the guest's x18 into a thread slot. Does Linux/Android keep it?
// The loop runs with no libc code in between (raw svc), so nothing in-process can touch x18; only the kernel could.
static uint64_t x18_loop(uint64_t iters, int use_svc) {
    uint64_t bad;
    __asm__ volatile(
        "mov x9, x18\n"
        "movz x18, #0x00ef\n movk x18, #0x00ad, lsl #16\n movk x18, #0x1234, lsl #32\n movk x18, #0x5678, lsl #48\n"
        "mov x11, x18\n mov x10, %[n]\n mov %[bad], #0\n"
        "1:\n"
        "cbz %[svc], 2f\n mov x8, #124\n svc #0\n"          // sched_yield
        "2:\n"
        "cmp x18, x11\n cinc %[bad], %[bad], ne\n mov x18, x11\n"
        "subs x10, x10, #1\n b.ne 1b\n"
        "mov x18, x9\n"
        : [bad] "=&r"(bad) : [n] "r"(iters), [svc] "r"((uint64_t)use_svc) : "x0", "x8", "x9", "x10", "x11", "memory", "cc");
    return bad;
}
static volatile int g_spin = 1;
static void *burner(void *a) { (void)a; volatile uint64_t x = 0; while (g_spin) x++; return NULL; }
static void on_alarm(int s) { (void)s; }   // our own code, built without shadow call stack: it never touches x18
static int t_x18(void) {
    long ncpu = sysconf(_SC_NPROCESSORS_ONLN); pthread_t th[64]; int n = ncpu * 2 > 64 ? 64 : (int)ncpu * 2;
    for (int i = 0; i < n; i++) pthread_create(&th[i], NULL, burner, NULL);   // oversubscribe the CPUs so the kernel preempts us
    struct sigaction sa = {0}; sa.sa_handler = on_alarm; sigaction(SIGALRM, &sa, NULL);
    struct itimerval it = {{0, 500}, {0, 500}}; setitimer(ITIMER_REAL, &it, NULL);   // and interrupt us with signals every 0.5 ms
    struct timespec t0, t1; clock_gettime(CLOCK_MONOTONIC, &t0);
    uint64_t bad_svc = x18_loop(200000, 1), bad_spin = x18_loop(1000000000ULL, 0);
    clock_gettime(CLOCK_MONOTONIC, &t1);
    struct itimerval off = {0}; setitimer(ITIMER_REAL, &off, NULL); g_spin = 0;
    for (int i = 0; i < n; i++) pthread_join(th[i], NULL);
    snprintf(g_out, sizeof g_out, "x18 preserved: %s (200k syscalls: %llu changes; %.1f s busy loop with %d competing threads + 0.5 ms SIGALRM: %llu changes)",
             bad_svc || bad_spin ? "NO" : "yes", (unsigned long long)bad_svc, (t1.tv_sec - t0.tv_sec) + (t1.tv_nsec - t0.tv_nsec) / 1e9, n, (unsigned long long)bad_spin);
    return bad_svc || bad_spin;
}

// ---- tpidr_el0: bionic's TLS pointer. Can it be swapped and does the kernel preserve a foreign value across switches?
static int t_tpidr(void) {
    uint64_t orig, got;
    __asm__ volatile("mrs %0, tpidr_el0" : "=r"(orig));
    __asm__ volatile(
        "msr tpidr_el0, %[m]\n mov x10, #20000\n"
        "1: mov x8, #124\n svc #0\n subs x10, x10, #1\n b.ne 1b\n"
        "mrs %[g], tpidr_el0\n msr tpidr_el0, %[o]\n"
        : [g] "=&r"(got) : [m] "r"(MAGIC), [o] "r"(orig) : "x0", "x8", "x10", "memory", "cc");
    snprintf(g_out, sizeof g_out, "tpidr_el0 = %#llx (bionic TLS); foreign value kept across 20k yields: %s", (unsigned long long)orig, got == MAGIC ? "yes" : "NO");
    return got != MAGIC;
}
static int t_tpidrro(void) {
    uint64_t v; g_detail = "mrs tpidrro_el0";
    __asm__ volatile("mrs %0, tpidrro_el0" : "=r"(v));
    snprintf(g_out, sizeof g_out, "tpidrro_el0 readable = %#llx (Horizon keeps its TLR pointer here; the rewriter maps reads to a slot)", (unsigned long long)v);
    return 0;
}

// ---- counters: Horizon games read cntpct_el0 directly; the Switch counter runs at 19.2 MHz
static int t_cntvct(void) {
    uint64_t f, a, b; g_detail = "mrs cntvct_el0 / cntfrq_el0";
    __asm__ volatile("mrs %0, cntfrq_el0" : "=r"(f));
    __asm__ volatile("isb\n mrs %0, cntvct_el0" : "=r"(a));
    usleep(100000);
    __asm__ volatile("isb\n mrs %0, cntvct_el0" : "=r"(b));
    snprintf(g_out, sizeof g_out, "cntvct_el0 readable; cntfrq_el0 = %llu Hz (Switch: 19200000); 100 ms = %llu ticks", (unsigned long long)f, (unsigned long long)(b - a));
    return 0;
}
static int t_cntpct(void) {
    uint64_t a; g_detail = "mrs cntpct_el0";
    __asm__ volatile("isb\n mrs %0, cntpct_el0" : "=r"(a));
    snprintf(g_out, sizeof g_out, "cntpct_el0 readable at EL0 (= %#llx); unpatched reads would work", (unsigned long long)a);
    return 0;
}

// ---- stack alignment: Horizon tolerates a misaligned sp; Apple faults (the rewriter rounds sub sp, sp, xN). Linux with SCTLR_EL1.SA0?
static int t_spalign(void) {
    g_detail = "load through an sp that is 8 mod 16";
    __asm__ volatile("mov x9, sp\n sub sp, sp, #8\n ldr x10, [sp]\n mov sp, x9\n" ::: "x9", "x10", "memory");
    snprintf(g_out, sizeof g_out, "misaligned sp access did NOT fault (rewrite would be optional)");
    return 0;
}

// ---- address space: nxnative reserves one guest block (5 GB for Tears of the Kingdom) and Monster Hunter Rise a 64 GB sparse GPU pool
static int t_vas(void) {
    char *p = g_out; size_t gb_ok = 0;
    static const size_t sizes[] = {4, 5, 8, 16, 32, 64, 128, 256, 512, 1024};
    for (unsigned i = 0; i < sizeof sizes / sizeof *sizes; i++) {
        void *m = mmap(NULL, sizes[i] << 30, PROT_NONE, MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE, -1, 0);
        if (m == MAP_FAILED) break;
        gb_ok = sizes[i]; munmap(m, sizes[i] << 30);
    }
    int local; p += sprintf(p, "largest PROT_NONE reservation: %zu GB; stack at %p (", gb_ok, (void *)&local);
    uintptr_t s = (uintptr_t)&local; int bits = 0; while (s >> bits) bits++;
    sprintf(p, "user VA ~%d bits); page size %ld", bits, sysconf(_SC_PAGESIZE));
    return gb_ok < 8;
}

// ---- executable memory: the host maps rewritten code; the patch area must sit within +-128 MB of the module (B reach)
typedef int (*fn_t)(void);
static const uint32_t RET42[] = {0x52800540, 0xd65f03c0};   // mov w0, #42; ret
static int call_code(void *rx) { __builtin___clear_cache((char *)rx, (char *)rx + 8); return ((fn_t)rx)(); }
static int t_exec(void) {
    char *p = g_out; int fails = 0; size_t pg = sysconf(_SC_PAGESIZE);
    // 1. anonymous RW -> RX (mprotect), what a loader does
    void *m = mmap(NULL, pg, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    memcpy(m, RET42, 8);
    int r1 = mprotect(m, pg, PROT_READ | PROT_EXEC) == 0 && call_code(m) == 42; fails += !r1;
    p += sprintf(p, "anon RW->RX: %s; ", r1 ? "ok" : strerror(errno));
    // 2. RWX in one mapping
    void *w = mmap(NULL, pg, PROT_READ | PROT_WRITE | PROT_EXEC, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    int r2 = w != MAP_FAILED && (memcpy(w, RET42, 8), call_code(w) == 42);
    p += sprintf(p, "anon RWX: %s; ", r2 ? "ok" : strerror(errno));
    // 3. a reserved 1 GB block with code placed at a fixed offset inside it (module image + patch area layout)
    char *blk = mmap(NULL, 1UL << 30, PROT_NONE, MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE, -1, 0);
    char *code = blk + (100UL << 20);
    int r3 = blk != MAP_FAILED && mprotect(code, pg, PROT_READ | PROT_WRITE) == 0 && (memcpy(code, RET42, 8), mprotect(code, pg, PROT_READ | PROT_EXEC) == 0) && call_code(code) == 42;
    fails += !r3;
    p += sprintf(p, "code inside reserved block: %s; ", r3 ? "ok" : strerror(errno));
    // 4. a file mapped executable from a writable dir: /data/local/tmp under adb shell, the app's files dir in the APK (PROBE_TMP), which W^X rules may forbid
    const char *dir = getenv("PROBE_TMP") ? getenv("PROBE_TMP") : "/data/local/tmp";
    char path[512]; snprintf(path, sizeof path, "%s/cpuprobe_codeXXXXXX", dir); int fd = mkstemp(path);
    int r4 = fd >= 0 && write(fd, RET42, 8) == 8 && ftruncate(fd, pg) == 0;
    void *f = r4 ? mmap(NULL, pg, PROT_READ | PROT_EXEC, MAP_PRIVATE, fd, 0) : MAP_FAILED;
    r4 = f != MAP_FAILED && call_code(f) == 42;
    p += sprintf(p, "file-backed RX from %s: %s; ", dir, r4 ? "ok" : fd < 0 ? "no temp file" : strerror(errno));
    if (fd >= 0) { close(fd); unlink(path); }
    // 5. a memfd mapped executable, and the same memfd mapped RW elsewhere (a dual-mapped code buffer)
    int mfd = syscall(__NR_memfd_create, "code", 0);
    int r5 = mfd >= 0 && ftruncate(mfd, pg) == 0;
    uint8_t *mw = r5 ? mmap(NULL, pg, PROT_READ | PROT_WRITE, MAP_SHARED, mfd, 0) : MAP_FAILED;
    void *mx = mw != MAP_FAILED ? mmap(NULL, pg, PROT_READ | PROT_EXEC, MAP_SHARED, mfd, 0) : MAP_FAILED;
    r5 = mx != MAP_FAILED && (memcpy(mw, RET42, 8), call_code(mx) == 42);
    sprintf(p, "memfd dual map RW+RX: %s", r5 ? "ok" : mfd < 0 ? "memfd_create failed" : strerror(errno));
    return fails;
}

// ---- runner
typedef struct { const char *name; int (*fn)(void); const char *on_signal; } test_t;
static const test_t TESTS[] = {
    {"x18", t_x18, "x18 test crashed"},
    {"tpidr_el0", t_tpidr, NULL},
    {"tpidrro_el0", t_tpidrro, "tpidrro_el0 not readable: reads must be rewritten (as nxnative does)"},
    {"cntvct_el0", t_cntvct, NULL},
    {"cntpct_el0", t_cntpct, "cntpct_el0 traps at EL0: reads must be rewritten (as nxnative does)"},
    {"sp alignment", t_spalign, "misaligned sp faults (SA0 set): the rewriter's sp rounding is required"},
    {"address space", t_vas, NULL},
    {"exec memory", t_exec, NULL},
};
int main(int argc, char **argv) {
    printf("cpuprobe: pid %d, %ld CPUs online\n", getpid(), sysconf(_SC_NPROCESSORS_ONLN));
    for (unsigned i = 0; i < sizeof TESTS / sizeof *TESTS; i++) {
        if (argc > 1 && !strstr(TESTS[i].name, argv[1])) continue;
        int pfd[2]; pipe(pfd); fflush(stdout);
        pid_t pid = fork();
        if (pid == 0) {
            close(pfd[0]); g_out[0] = 0; int rc = TESTS[i].fn();
            write(pfd[1], g_out, strlen(g_out)); _exit(rc ? 1 : 0);
        }
        close(pfd[1]); char buf[512] = {0}; ssize_t n = read(pfd[0], buf, sizeof buf - 1); (void)n; close(pfd[0]);
        int st; waitpid(pid, &st, 0);
        if (WIFSIGNALED(st))
            printf("[%-13s] SIGNAL %d (%s) -> %s\n", TESTS[i].name, WTERMSIG(st), strsignal(WTERMSIG(st)), TESTS[i].on_signal ? TESTS[i].on_signal : "unexpected crash");
        else
            printf("[%-13s] %s %s\n", TESTS[i].name, WEXITSTATUS(st) ? "WARN" : "ok  ", buf);
    }
    return 0;
}
