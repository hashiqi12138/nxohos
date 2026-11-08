/* t_mini_nso.c — M0.5 重写码真机测试（独立可执行，TC0-5-1 / TC0-5-2）
 *
 * 五个 TEST（B 文档 §M0.5，全部在真机测试进程内执行；PC 不编译）：
 *   mini_nso_svc_gate      重写后的 svc #0x27 进宿主 gate 并返回 42
 *   mini_nso_x18           guest-x18 槽读到 0xDEADBEEF 且宿主 x18 未被破坏
 *   mini_nso_tls           guest 直读 tpidr_el0 非零（KTLS 槽前提）
 *   mini_nso_timer         guest 读 cntpct_el0 严格递增
 *   host_tls_survives_guest（TC0-5-2）宿主 musl TLS 在 guest 调用前后一致
 *
 * 构建：build_probe.py 以设备目标编译本文件 + mini_nso.S + mini_host.c
 * （-ffixed-x18，静态链接），产物推到真机 hdc shell 直接执行。
 */
#if defined(__aarch64__) && !defined(DM_OHOS_HOST_TEST)

#include "../mini_host.h"
#include "dmtest.h"

#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

/* 直接链接 mini_nso.S 符号（独立可执行无需 blob 偏移表） */
extern uint64_t mini_svc_fn(MiniCtx *ctx);
extern uint64_t mini_x18_fn(MiniCtx *ctx);
extern uint64_t mini_tls_fn(MiniCtx *ctx);
extern uint64_t mini_timer_fn(MiniCtx *ctx);

/* ---------- 全局 guest 状态（每个 TEST 入口经 reset_guest_state 清零） ---------- */
static MiniCtx  g_ctx;
static uint64_t g_gate_calls;
static uint64_t g_x18_canary;
static int      g_loaded_once;   /* 占位：blob 模式下此处会重载 NSO 镜像 */

static uint64_t test_gate_dispatch(void *ctx, uint32_t gate_id)
{
    (void)ctx;
    g_gate_calls++;
    return gate_id == MINI_GATE_SVC ? MINI_SVC_RESULT : 0;
}

/* dmtest reset 钩子：清 gate 表/canary、恢复 ctx（对应"重载 NSO、清 gate 表"） */
static void reset_guest_state(void)
{
    g_gate_calls = 0;
    g_ctx.gate_entry = test_gate_dispatch;
    g_ctx.slot_x18   = MINI_SLOT_X18_VALUE;
    g_x18_canary     = 0xC0DEBABEC0DEBABEULL;
    mini_host_x18_write(g_x18_canary);
    g_loaded_once = 1;
}

static int host_x18_intact(void)
{
    return mini_host_x18_read() == g_x18_canary;
}

/* run_guest_call：按名字派发（与 B 文档测试用例命名一致） */
static uint64_t run_guest_call(const char *fn)
{
    if (strcmp(fn, "mini_svc_fn") == 0)
        return mini_svc_fn(&g_ctx);
    if (strcmp(fn, "mini_x18_fn") == 0)
        return mini_x18_fn(&g_ctx);
    if (strcmp(fn, "mini_tls_fn") == 0)
        return mini_tls_fn(&g_ctx);
    if (strcmp(fn, "mini_timer_fn") == 0)
        return mini_timer_fn(&g_ctx);
    return 0;
}

/* ---------- TEST（与 B 文档 §M0.5 逐字对应） ---------- */

TEST(mini_nso_svc_gate)
{                          /* 迷你 NSO 内执行 svc #0x27，期望进宿主 gate 并返回 42 */
    reset_guest_state();   /* 每个 TEST 显式 reset：重载 NSO、清 gate 表 */
    EXPECT_EQ(run_guest_call("mini_svc_fn"), 42);
    EXPECT_EQ(g_gate_calls, 1);    /* 确实走了 gate（而非内联捷径） */
}

TEST(mini_nso_x18)
{
    reset_guest_state();
    EXPECT_EQ(run_guest_call("mini_x18_fn"), 0xDEADBEEF);
    EXPECT_TRUE(host_x18_intact());   /* 宿主侧 x18 未被破坏 */
}

TEST(mini_nso_tls)
{
    reset_guest_state();
    EXPECT_NE(run_guest_call("mini_tls_fn"), 0);
}

TEST(mini_nso_timer)
{
    reset_guest_state();
    uint64_t a = run_guest_call("mini_timer_fn");
    uint64_t b = run_guest_call("mini_timer_fn");
    EXPECT_GT(b, a);               /* 严格递增；同 tick 翻转风险由 FLAKE 规则兜底 */
}

TEST(host_tls_survives_guest)
{   /* TC0-5-2：宿主自己的 TLS 在 guest 调用后可用 */
    reset_guest_state();
    pthread_t before = pthread_self();
    run_guest_call("mini_x18_fn");
    EXPECT_EQ(pthread_self(), before);   /* musl TLS 未被踩坏 */
    EXPECT_TRUE(host_x18_intact());
}

int main(void)
{
    dmtest_set_reset(reset_guest_state);   /* dmtest_run_all 每用例入口自动调用 */
    int failed = dmtest_run_all();
    printf("t_mini_nso: %s\n", failed ? "FAIL" : "PASS");
    return failed;
}

#else /* PC：空 TU（真机专用），保证误编译也不报错 */

#include <stdio.h>

int main(void)
{
    printf("t_mini_nso: SKIP (requires aarch64 device)\n");
    return 0;
}

#endif
