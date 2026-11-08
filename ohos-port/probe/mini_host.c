/* mini_host.c — 重写形态迷你 NSO 的宿主侧实现（仅 aarch64 目标有意义；
 * PC 语法检查时函数体为桩。） */
#include "mini_host.h"

#include <dlfcn.h>
#include <fcntl.h>
#include <link.h>
#include <sys/mman.h>
#include <string.h>
#include <unistd.h>

/* 宿主 gate：MINI_GATE_SVC → 42（其余 → 0）。纯逻辑，两个编译分支共用；
 * libprobe.so 链接 -Wl,--no-undefined 时缺失本定义会在构建期暴露。 */
uint64_t mini_gate_dispatch(void *ctx, uint32_t gate_id)
{
    (void)ctx;
    return gate_id == MINI_GATE_SVC ? MINI_SVC_RESULT : 0;
}

_Static_assert(offsetof(MiniCtx, gate_entry) == 0, ".S 依赖 gate_entry@0");
_Static_assert(offsetof(MiniCtx, slot_x18) == 8, ".S 依赖 slot_x18@8");

#if defined(__aarch64__) && !defined(DM_OHOS_HOST_TEST)

/* struct MiniGuest 定义在 mini_host.h（probe_regs.c 按值持有句柄需要完整类型） */

struct mini_seg {
    const char *path;
    size_t      off;
};

static int mini_find_seg(struct dl_phdr_info *info, size_t size, void *data)
{
    (void)size;
    struct mini_seg *s = (struct mini_seg *)data;
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

/* 第三方案：借用真实 .so 的可执行段（MAP_PRIVATE）当代码容器。
 * 真机上匿名页 execmem 一律被拒（EINVAL/EACCES），但文件映射的代码页
 * 允许 mprotect 成 RW 再改回 RX —— 这正是 patch_nso 的落地形态。 */
static void *map_via_so_seg(size_t len)
{
    struct mini_seg s = { NULL, 0 };
    dl_iterate_phdr(mini_find_seg, &s);
    if (!s.path)
        return MAP_FAILED;
    int fd = open(s.path, O_RDONLY);
    if (fd < 0)
        return MAP_FAILED;
    long pg = sysconf(_SC_PAGESIZE);
    size_t maplen = (len + (size_t)pg - 1) & ~((size_t)pg - 1);
    void *p = mmap(NULL, maplen, PROT_READ | PROT_EXEC, MAP_PRIVATE, fd, (off_t)s.off);
    close(fd);
    if (p == MAP_FAILED)
        return MAP_FAILED;
    if (mprotect(p, maplen, PROT_READ | PROT_WRITE) != 0) {
        munmap(p, maplen);
        return MAP_FAILED;
    }
    return p;
}

/* 第三方案：先落到应用沙盒文件，再按 MAP_PRIVATE|PROT_EXEC 映射（dlopen 走的正是
 * 这条路径）。真机上匿名页 execmem 被拒，但已落盘文件的可执行映射未必被拒。 */
static void *map_via_sandbox_file(const unsigned char *code, size_t len)
{
    const char *path = "/data/storage/el2/base/files/.dmjit_guest";
    int fd = open(path, O_RDWR | O_CREAT | O_TRUNC, 0600);
    if (fd < 0)
        return MAP_FAILED;
    if (write(fd, code, len) != (ssize_t)len) {
        close(fd);
        unlink(path);
        return MAP_FAILED;
    }
    long pg = sysconf(_SC_PAGESIZE);
    size_t maplen = (len + (size_t)pg - 1) & ~((size_t)pg - 1);
    void *p = mmap(NULL, maplen, PROT_READ | PROT_EXEC, MAP_PRIVATE, fd, 0);
    close(fd);
    return p;
}

MiniGuest mini_guest_load(const unsigned char *code, size_t len)
{
    if (!code || len == 0)
        return NULL;
    /* 真机 SELinux 拒绝匿名页 mprotect(W→X)，所以依次尝试：
     *   1) 直接带 EXEC 的匿名映射（宽松环境）
     *   2) 匿名 RW → mprotect RX（标准做法）
     *   3) 落沙盒文件后按 MAP_PRIVATE|PROT_EXEC 映射（dlopen 同款路径）
     *   4) 借用已加载 .so 的可执行段（本机兜底） */
    void *m = mmap(NULL, len, PROT_READ | PROT_WRITE | PROT_EXEC,
                   MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (m != MAP_FAILED) {
        memcpy(m, code, len);
        __builtin___clear_cache((char *)m, (char *)m + len);
    } else {
        m = mmap(NULL, len, PROT_READ | PROT_WRITE,
                 MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
        if (m != MAP_FAILED) {
            memcpy(m, code, len);
            __builtin___clear_cache((char *)m, (char *)m + len);
            if (mprotect(m, len, PROT_READ | PROT_EXEC) != 0) {
                munmap(m, len);
                m = MAP_FAILED;
            }
        }
        if (m == MAP_FAILED) {
            m = map_via_sandbox_file(code, len);
            if (m != MAP_FAILED)
                __builtin___clear_cache((char *)m, (char *)m + len);
        }
        if (m == MAP_FAILED) {
            m = map_via_so_seg(len);
            if (m == MAP_FAILED)
                return NULL;
            memcpy(m, code, len);
            __builtin___clear_cache((char *)m, (char *)m + len);
            if (mprotect(m, (len + 4095) & ~(size_t)4095,
                         PROT_READ | PROT_EXEC) != 0) {
                return NULL;
            }
        }
    }
    struct MiniGuest *g = (struct MiniGuest *)mmap(
        NULL, sizeof *g, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (g == MAP_FAILED) {
        munmap(m, len);
        return NULL;
    }
    g->code = (unsigned char *)m;
    g->len  = len;
    return g;
}

void mini_guest_free(MiniGuest g_)
{
    struct MiniGuest *g = (struct MiniGuest *)g_;
    if (!g)
        return;
    munmap(g->code, g->len);
    munmap(g, sizeof *g);
}

uint64_t mini_guest_call(MiniGuest g_, size_t entry_off, MiniCtx *ctx)
{
    struct MiniGuest *g = (struct MiniGuest *)g_;
    uint64_t (*fn)(MiniCtx *) = (uint64_t (*)(MiniCtx *))(g->code + entry_off);
    return fn(ctx);
}

uint64_t mini_host_x18_read(void)
{
    uint64_t v;
    asm volatile("mov %0, x18" : "=r"(v));
    return v;
}

void mini_host_x18_write(uint64_t v)
{
    /* -ffixed-x18 下编译器不缓存 x18，无需 clobber 声明（写 x18 正是本句语义） */
    asm volatile("mov x18, %0" :: "r"(v));
}

#else /* PC 桩：仅保证语法可编 */

MiniGuest mini_guest_load(const unsigned char *code, size_t len)
{
    (void)code;
    (void)len;
    return NULL;
}

void mini_guest_free(MiniGuest g)
{
    (void)g;
}

uint64_t mini_guest_call(MiniGuest g, size_t entry_off, MiniCtx *ctx)
{
    (void)g; (void)entry_off; (void)ctx;
    return 0;
}

uint64_t mini_host_x18_read(void)
{
    return 0;
}

void mini_host_x18_write(uint64_t v)
{
    (void)v;
}

#endif
