/* mini_host.h — 重写形态迷你 NSO 的宿主侧装载/调用/gate（M0.5g）
 *
 * guest ABI（与 mini_nso.S 严格对齐）：
 *   - 调用约定：x0 = MiniCtx*；返回值在 x0；
 *   - MiniCtx 布局：gate_entry 在偏移 0，slot_x18 在偏移 8（.S 内硬编码）；
 *   - "svc #0x27" 的重写形态 = 调 ctx->gate_entry(ctx, MINI_GATE_SVC)，宿主 gate
 *     返回 42；guest x18 读取的重写形态 = 读 ctx->slot_x18（槽基址方案）。
 */
#ifndef MINI_HOST_H
#define MINI_HOST_H

#include <stddef.h>
#include <stdint.h>

#define MINI_GATE_SVC        1u
#define MINI_SVC_RESULT      42u
#define MINI_SLOT_X18_VALUE  0xDEADBEEFULL

typedef struct MiniCtx {
    /* 偏移 0：gate 分发表入口（.S：ldr x9,[x0,#0]） */
    uint64_t (*gate_entry)(void *ctx, uint32_t gate_id);
    /* 偏移 8：guest-x18 槽（.S：ldr x0,[x0,#8]） */
    uint64_t slot_x18;
} MiniCtx;

/* blob 内四个入口的偏移键：由 build_probe.py 从符号表生成 mini_nso_blob.h 时
 * #define MINI_OFF_SVC / MINI_OFF_X18 / MINI_OFF_TLS / MINI_OFF_TIMER（字节偏移）。
 * 此处故意不提供缺省值——防止生成头缺失时用到错误偏移（probe_regs.c 仅在
 * __has_include 成功时使用这些键）。 */

/* RX 映射的机码页句柄。定义放头里：probe_regs.c 按值持有 MiniGuest，
 * 仅有前向声明会因不完整类型无法编译（真机构建实测）。 */
typedef struct MiniGuest {
    unsigned char *code;
    size_t         len;
} *MiniGuest;

/* mmap RW → memcpy → clear_cache → mprotect RX（顺带就是 M0.5f 的实用路径） */
MiniGuest mini_guest_load(const unsigned char *code, size_t len);
void      mini_guest_free(MiniGuest g);
uint64_t  mini_guest_call(MiniGuest g, size_t entry_off, MiniCtx *ctx);

/* 宿主 gate：MINI_GATE_SVC → 42（其余 → 0） */
uint64_t mini_gate_dispatch(void *ctx, uint32_t gate_id);

/* 宿主 x18 金丝雀读写（宿主代码须以 -ffixed-x18 编译，防编译器占用） */
uint64_t mini_host_x18_read(void);
void     mini_host_x18_write(uint64_t v);

#endif /* MINI_HOST_H */
