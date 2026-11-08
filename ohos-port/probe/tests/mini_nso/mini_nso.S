/* mini_nso.S — 重写形态迷你 NSO 机码（B 文档 §M0.5 g）
 *
 * 四个入口分别验证一种重写语义（guest ABI：x0=MiniCtx*，返回值 x0）：
 *   mini_svc_fn    "svc #0x27" 的重写形态：调 ctx->gate_entry(ctx, GATE_SVC)，
 *                  宿主 gate 返回 42 —— gate 表传参机制可运行；
 *   mini_x18_fn    guest x18 读取的重写形态：读 ctx->slot_x18（槽基址方案），
 *                  宿主侧真实 x18 不被占用（金丝雀由宿主比对）；
 *   mini_tls_fn    未重写的 tpidr_el0 直读（KTLS 槽前提：musl TCB 指针非零）；
 *   mini_timer_fn  未重写的 cntpct_el0 直读（guest 可读系统计数器且单调）。
 *
 * 仅 aarch64 汇编；build_probe.py 对设备目标汇编本文件，objcopy 出 .text
 * 生成 mini_nso_blob.h（代码 + MINI_OFF_* 入口字节偏移）。
 */
#if defined(__aarch64__)

    .arch armv8-a
    .text
    .align 4

/* uint64_t mini_svc_fn(MiniCtx *ctx) */
    .globl mini_svc_fn
    .type  mini_svc_fn, %function
mini_svc_fn:
    ldr     x9, [x0, #0]        /* ctx->gate_entry（偏移 0，见 mini_host.h） */
    mov     w1, #1              /* MINI_GATE_SVC */
    blr     x9                  /* gate 返回值已在 x0 */
    ret
    .size mini_svc_fn, . - mini_svc_fn

/* uint64_t mini_x18_fn(MiniCtx *ctx) */
    .globl mini_x18_fn
    .type  mini_x18_fn, %function
mini_x18_fn:
    ldr     x0, [x0, #8]        /* ctx->slot_x18（偏移 8） */
    ret
    .size mini_x18_fn, . - mini_x18_fn

/* uint64_t mini_tls_fn(MiniCtx *ctx) */
    .globl mini_tls_fn
    .type  mini_tls_fn, %function
mini_tls_fn:
    mrs     x0, tpidr_el0       /* 未重写：KTLS 槽前提验证 */
    ret
    .size mini_tls_fn, . - mini_tls_fn

/* uint64_t mini_timer_fn(MiniCtx *ctx) */
    .globl mini_timer_fn
    .type  mini_timer_fn, %function
mini_timer_fn:
    mrs     x0, cntpct_el0      /* 未重写：guest 计数器可读 */
    ret
    .size mini_timer_fn, . - mini_timer_fn

#endif /* __aarch64__ */
