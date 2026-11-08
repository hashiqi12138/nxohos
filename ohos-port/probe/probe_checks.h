/* probe_checks.h — 探针内部接口（probe_main.c 编排 ← 各检查实现）
 *
 * 约定：
 *  - 每个检查在独立 fork 子进程中执行（B 文档 M0 探针崩溃隔离），实现函数只写
 *    ProbeReport 中属于自己的字段（报告本身在 MAP_SHARED 匿名映射里）；
 *  - 子进程内禁止 hilog/stdio（可能持锁），需要日志一律写 note 字段，由父进程汇总；
 *  - 正常返回 bool（= ok），异常 trap 由父进程 waitpid 归因（crashed/sig）。
 */
#ifndef PROBE_CHECKS_H
#define PROBE_CHECKS_H

#include "probe_report.h"

#ifdef __cplusplus
extern "C" {
#endif

/* M0.2 + M0.3：Vulkan→VK_OHOS_surface→swapchain→300 帧纯色上屏 + BCn/ASTC 探测。
 * 同一子进程执行（surface 链路强状态依赖）；窗口由 XComponent 回调注入。 */
bool probe_vk_run(ProbeReport *r);

/* M0.4：三段各自独立（每段自带 5GB 预留建立工作区，互不依赖，可各自隔离执行） */
bool probe_mem_reserve5g(ProbeReport *r);   /* 一次 5GB PROT_NONE 预留 */
bool probe_mem_headroom(ProbeReport *r);    /* base+5GB 处 MAP_FIXED_NOREPLACE 衔接 +8GB */
bool probe_mem_commit(ProbeReport *r);      /* base+1GB 处 commit 2GB + memset 0xAB */

/* M0.5：七项（每项独立，可各自隔离执行） */
bool probe_tpidr_rw(ProbeReport *r);        /* a) tpidr_el0 哨兵写读恢复 */
bool probe_tpidrro(ProbeReport *r);         /* b) tpidrro_el0 EL0 可读 */
bool probe_cntpct(ProbeReport *r);          /* c) 按实读频率换算 + 10% 容差 */
bool probe_tls_per_thread(ProbeReport *r);  /* d) 每线程 tpidr_el0 隔离 */
bool probe_futex(ProbeReport *r);           /* e) futex 等待/唤醒语义 */
bool probe_wx(ProbeReport *r);              /* f) RW→mprotect(RX) 代码页执行 */
bool probe_mini_nso(ProbeReport *r);        /* g) 重写码实测（mini_host.c 运行时装载） */
bool probe_aot_nso(ProbeReport *r);         /* h) 重写码 AOT 形态（dlopen 打包的 libmininso.so） */
void probe_aot_preload(void);               /* h) 父进程内预加载 + 取符号（子进程禁 dlopen） */

/* probe_vk.c：XComponent OnSurfaceCreated 注入 OHNativeWindow*（线程安全，仅赋值一次） */
void probe_vk_set_window(void *window);

/* probe_vk.c：等待窗口注入（父进程 fork 前调用消除时序竞态；XComponent
 * OnSurfaceCreated 在 Ability 首帧后才触发）。返回 true=已就绪。 */
bool probe_vk_wait_window(int seconds);

#ifdef __cplusplus
}
#endif

#endif /* PROBE_CHECKS_H */
