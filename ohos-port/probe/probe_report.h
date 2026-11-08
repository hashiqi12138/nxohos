/* probe_report.h — M0 探针结论数据载体（《设备能力报告》源结构）
 *
 * JSON 键名与 B 文档 TC 判定严格对齐：
 *   TC0-2-1 读 vk.has_vk_ohos_surface / vk.api_version
 *   TC0-3-1 读 bcn 表（恰好 16 个 VK_FORMAT_* 键）/ bcn_all
 *   TC0-4-1 读 mem.headroom_contiguous
 *   porter 的 capability.json 由本报告派生（M4.2）。
 */
#ifndef PROBE_REPORT_H
#define PROBE_REPORT_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define PROBE_BCn_COUNT 16

/* 与 probe_vk.c 的 BCN_FORMATS[] 一一对应的稳定 JSON 键（探测全集：
 * 游戏 16 种 BC 格式 + sRGB 变体，缺一个都可能触发 M3.4 转码） */
extern const char *const probe_bcn_keys[PROBE_BCn_COUNT];

/* 单项检查结论：ok=业务结论；crashed=子进程 trap（SIGSEGV/SIGILL/SIGBUS/被杀） */
typedef struct {
    bool ok;
    bool crashed;
    int  sig;             /* crashed 时为信号号，否则 0 */
    char note[256];       /* 可选备注（如 tpidrro: "zero"/"nonzero"；W^X 需容六路诊断） */
} ProbeItem;

typedef struct {
    /* M0.2 Vulkan + VK_OHOS_surface */
    ProbeItem  vk_surface;        /* JSON: vk.has_vk_ohos_surface = ok */
    uint32_t   vk_api_version;    /* packed：VK_API_VERSION_MAJOR/MINOR/PATCH */
    char       vk_device_name[256];
    char       vk_driver_info[128]; /* 驱动版本 + SPIR-V 环境版本（G0.2 留档） */
    double     present_ms;        /* 仅记录不判定（FIFO@60Hz 下会阻塞至 vsync） */

    /* M0.3 BCn / ASTC */
    bool       bcn[PROBE_BCn_COUNT];
    bool       bcn_all;           /* 语义：上表 16 项全部 true */
    bool       astc_4x4;
    bool       astc_8x8;

    /* M0.4 mmap 大地址预留 */
    ProbeItem  mem_reserve_5g;    /* JSON: mem.reserve_5g = ok */
    ProbeItem  mem_headroom_8g;   /* JSON: mem.reserve_headroom_8g = ok */
    bool       headroom_contiguous; /* 显式区分：总量 ok vs 连续 ok */
    ProbeItem  mem_commit_2g;     /* JSON: mem.commit_2g_ok = ok */

    /* M0.5 寄存器 / futex / 代码页语义 */
    ProbeItem  tpidr_rw;          /* JSON: regs.tpidr_rw = ok */
    ProbeItem  tpidrro;           /* JSON: regs.tpidrro_readable = ok；note=zero/nonzero */
    ProbeItem  cntpct;            /* JSON: regs.cntpct_ok = ok */
    uint64_t   cntfrq_hz;         /* 实测定时器频率，供宿主 guest_ticks() 换算核对 */
    ProbeItem  tls_per_thread;    /* JSON: regs.tls_per_thread = ok */
    ProbeItem  futex;             /* JSON: regs.futex_ok = ok */
    ProbeItem  wx;                /* JSON: regs.wx_ok = ok */
    ProbeItem  mini_nso;          /* JSON: regs.rewritten_nso_runs = ok（运行时装载，真机不可行） */
    ProbeItem  aot_nso;           /* JSON: regs.aot_nso_runs = ok（打包 .so 正文，M0.5h） */

    /* 元信息 */
    char       started_at[40];    /* ISO8601 UTC */
    char       finished_at[40];
    char       device_model[128];
    char       os_version[128];   /* const.product.software.version */
    char       build[48];         /* 构建戳（DM_PROBE_BUILD），用于确认设备上跑的是哪一版 */
} ProbeReport;

void probe_report_init(ProbeReport *r);

/* 序列化为 malloc 字符串（调用方 free）。成功返回缓冲区，失败返回 NULL。
 * *out_len 可为 NULL；非 NULL 时写入不含结尾 '\0' 的长度。 */
char *probe_report_json(const ProbeReport *r, size_t *out_len);

/* 写文件（/data/local/tmp/probe_report.json）。返回 0 成功，-1 失败。 */
int probe_report_write_json(const ProbeReport *r, const char *path);

/* 将已序列化的 JSON 串写文件（hilog 输出与落盘共用，避免二次组装）。
 * len 为不含 '\0' 的长度。返回 0 成功，-1 失败。 */
int probe_report_write_str(const char *path, const char *js, size_t len);

/* 供 t_probe_units 使用的纯逻辑助手 */
int  probe_json_escape(const char *src, char *dst, size_t dst_cap);
bool probe_bcn_all(const bool *bcn);

/* M0.5a tpidr 可写性判定数学：SENTINEL = t0 ^ 0x5555...（非自身、非恒真），
 * 写被静默丢弃(WI)时 t1==t0≠SENTINEL → false。 */
uint64_t probe_sentinel(uint64_t t0);
bool probe_tpidr_writable_check(uint64_t t0, uint64_t t1);

/* M0.5c cntpct 判定数学：expect = freq*elapsed_ms/1000（按实读频率换算，非硬编码
 * 1GHz），slack = expect/10（10% 容差）；ok = b>a 且 |b-a - expect| ≤ slack。
 * freq==0（cntfrq_el0 异常）直接 false。 */
bool probe_cntpct_judge(uint64_t freq, uint64_t a, uint64_t b, uint64_t elapsed_ms);

#ifdef __cplusplus
}
#endif

#endif /* PROBE_REPORT_H */
