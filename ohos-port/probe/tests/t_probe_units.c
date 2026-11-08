/* t_probe_units.c — M0 探针 PC 可跑单元测试（T0，B 文档 §测试分层）
 *
 * 覆盖（无真机依赖）：
 *   1. probe_json_escape 转义（" \ \n \r \t \b \f 控制字符、UTF-8 直通、容量计算）
 *   2. bcn 16 键表（数量/非空/前缀/唯一性）与 probe_bcn_all 语义
 *   3. 默认 ProbeReport 的 JSON schema 契约（键齐全、值回填、嵌套转义）
 *   4. cntpct 期望值数学（按实读频率换算 + 10% 容差边界）
 *   5. tpidr 哨兵数学（非恒真：SENTINEL 恒 ≠ t0；写丢弃时判定 false）
 *   6. futex + W^X 运行时测试（仅 __linux__，Windows/其他平台编译为空）
 *
 * 断言宏 = probe/tests/dmtest.h；运行：见 tools/verify/run_unit_tests.py
 * （用 PC 侧 clang/gcc 编译本文件 + probe_json.c + dmtest.c 后直接执行）。
 */
#include "dmtest.h"
#include "probe_report.h"

#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

/* ---------- 1. JSON 转义 ---------- */
TEST(json_escape_plain)
{
    char out[64];
    int  need = probe_json_escape("abc", out, sizeof out);
    EXPECT_EQ(need, 3);
    EXPECT_STREQ(out, "abc");
}

TEST(json_escape_specials)
{
    char out[64];
    int  need = probe_json_escape("a\"b\\c\nd\re\tf\bg\fh", out, sizeof out);
    /* 8 个原样字符 + 7 个双字符转义（" \ \n \r \t \b \f）= 22 */
    EXPECT_STREQ(out, "a\\\"b\\\\c\\nd\\re\\tf\\bg\\fh");
    EXPECT_EQ(need, 22);
}

TEST(json_escape_control_u)
{
    char out[64];
    int  need = probe_json_escape("\x01", out, sizeof out);
    EXPECT_STREQ(out, "\\u0001");
    EXPECT_EQ(need, 6);
}

TEST(json_escape_utf8_passthrough)
{
    /* UTF-8 多字节直通（不做 \\u 展开） */
    char out[64];
    const char *s = "\xE4\xB8\xAD";   /* 中 */
    int  need = probe_json_escape(s, out, sizeof out);
    EXPECT_EQ(need, 3);
    EXPECT_STREQ(out, s);
}

TEST(json_escape_capacity_math)
{
    /* dst==NULL：只计算长度；容量不足：写入截断、返回值仍是所需长度。
     * 截断策略（实现契约）：独立写游标 wp，可写容量 = cap-1；一个"段"
     * （普通字符 1 字节 / 转义序列 2-6 字节）放不下则整段跳过——
     * 已写前缀总是合法 JSON，无半截转义。 */
    const char *s = "ab\"cd";
    EXPECT_EQ(probe_json_escape(s, NULL, 0), 6);   /* a b \" c d → 6 */
    char small[4];
    int  need = probe_json_escape(s, small, sizeof small);
    EXPECT_EQ(need, 6);
    /* cap=4：可写 3 字节。a,b 写入；\" 段（2 字节）在 wp=2 时 wp+2>3 整段跳过；
     * c（1 字节）恰好 wp=2+1=3≤3 写入；d 跳过 → "abc" */
    EXPECT_STREQ(small, "abc");
    char ok_cap[16];
    EXPECT_EQ(probe_json_escape(s, ok_cap, sizeof ok_cap), 6);
    EXPECT_STREQ(ok_cap, "ab\\\"cd");   /* JSON 转义后的内容：a b \" c d */
}

/* ---------- 2. bcn 表与 probe_bcn_all ---------- */
TEST(bcn_keys_contract)
{
    const char *seen[PROBE_BCn_COUNT] = { 0 };
    for (int i = 0; i < PROBE_BCn_COUNT; i++) {
        EXPECT_NOT_NULL(probe_bcn_keys[i]);
        EXPECT_TRUE(strncmp(probe_bcn_keys[i], "VK_FORMAT_", 10) == 0);
        for (int j = 0; j < i; j++) {   /* 唯一性（JSON 键不得重复） */
            if (strcmp(probe_bcn_keys[i], probe_bcn_keys[j]) == 0)
                EXPECT_TRUE(!"duplicate bcn key");
        }
        seen[i] = probe_bcn_keys[i];
    }
    EXPECT_TRUE(seen[PROBE_BCn_COUNT - 1] != NULL);
    /* 全序逐项锁定（跨文件契约：与 probe_vk.c BCN_FORMATS[] 严格同序，
     * probe_vk.c 侧另有 16 条 _Static_assert 互为镜像） */
    static const char *const want[PROBE_BCn_COUNT] = {
        "VK_FORMAT_BC1_RGB_UNORM_BLOCK",  "VK_FORMAT_BC1_RGB_SRGB_BLOCK",
        "VK_FORMAT_BC1_RGBA_UNORM_BLOCK", "VK_FORMAT_BC1_RGBA_SRGB_BLOCK",
        "VK_FORMAT_BC2_UNORM_BLOCK",      "VK_FORMAT_BC2_SRGB_BLOCK",
        "VK_FORMAT_BC3_UNORM_BLOCK",      "VK_FORMAT_BC3_SRGB_BLOCK",
        "VK_FORMAT_BC4_UNORM_BLOCK",      "VK_FORMAT_BC4_SNORM_BLOCK",
        "VK_FORMAT_BC5_UNORM_BLOCK",      "VK_FORMAT_BC5_SNORM_BLOCK",
        "VK_FORMAT_BC6H_UFLOAT_BLOCK",    "VK_FORMAT_BC6H_SFLOAT_BLOCK",
        "VK_FORMAT_BC7_UNORM_BLOCK",      "VK_FORMAT_BC7_SRGB_BLOCK",
    };
    for (int i = 0; i < PROBE_BCn_COUNT; i++)
        EXPECT_STREQ(probe_bcn_keys[i], want[i]);
}

TEST(bcn_all_semantics)
{
    bool b[PROBE_BCn_COUNT];
    memset(b, 1, sizeof b);
    EXPECT_TRUE(probe_bcn_all(b));
    for (int i = 0; i < PROBE_BCn_COUNT; i++) {
        b[i] = false;                       /* 任一 false → false */
        EXPECT_FALSE(probe_bcn_all(b));
        b[i] = true;
    }
    EXPECT_TRUE(probe_bcn_all(b));
}

/* ---------- 3. 默认报告 JSON schema ---------- */
TEST(json_default_schema_keys)
{
    ProbeReport r;
    probe_report_init(&r);
    char *js = probe_report_json(&r, NULL);
    EXPECT_NOT_NULL(js);
    if (!js)
        return;

    /* 顶层分组与 TC 判定键齐全 */
    EXPECT_TRUE(strstr(js, "\"meta\":{\"started_at\":") != NULL);
    EXPECT_TRUE(strstr(js, "\"vk\":{\"has_vk_ohos_surface\":false") != NULL);
    EXPECT_TRUE(strstr(js, "\"api_version\":\"0.0.0\"") != NULL);
    EXPECT_TRUE(strstr(js, "\"api_version_raw\":0") != NULL);
    EXPECT_TRUE(strstr(js, "\"bcn_all\":false") != NULL);
    EXPECT_TRUE(strstr(js, "\"astc\":{\"4x4\":false,\"8x8\":false}") != NULL);
    EXPECT_TRUE(strstr(js, "\"mem\":{\"reserve_5g\":false") != NULL);
    EXPECT_TRUE(strstr(js, "\"headroom_contiguous\":false") != NULL);
    EXPECT_TRUE(strstr(js, "\"regs\":{\"tpidr_rw\":false") != NULL);
    EXPECT_TRUE(strstr(js, "\"cntpct_ok\":false") != NULL);
    EXPECT_TRUE(strstr(js, "\"rewritten_nso_runs\":false") != NULL);

    /* bcn 表恰好 16 个 VK_FORMAT_* 键 */
    int count = 0;
    for (const char *p = js; (p = strstr(p, "\"VK_FORMAT_")) != NULL; p++)
        count++;
    EXPECT_EQ(count, PROBE_BCn_COUNT);

    /* checks 明细齐全（crashed 归因字段） */
    EXPECT_TRUE(strstr(js, "\"checks\":{\"vk_surface\":{\"ok\":false,\"crashed\":false,"
                             "\"sig\":0") != NULL);
    EXPECT_TRUE(strstr(js, "\"mini_nso\":") != NULL);
    free(js);
}

TEST(json_value_roundtrip_and_escape)
{
    ProbeReport r;
    probe_report_init(&r);
    r.vk_surface.ok      = true;
    /* packed VkApiVersion（不依赖 vulkan.h）：major=1 minor=3 patch=243 */
    r.vk_api_version     = (1u << 22) | (3u << 12) | 243u;
    snprintf(r.vk_device_name, sizeof r.vk_device_name, "Maloon \"L\"900");
    r.bcn[0] = true;
    r.bcn_all = false;
    r.cntfrq_hz = 19200000ULL;
    r.tpidrro.ok = true;
    snprintf(r.tpidrro.note, sizeof r.tpidrro.note, "nonzero");
    r.vk_surface.sig = 0;

    char *js = probe_report_json(&r, NULL);
    EXPECT_NOT_NULL(js);
    if (!js)
        return;
    EXPECT_TRUE(strstr(js, "\"has_vk_ohos_surface\":true") != NULL);
    EXPECT_TRUE(strstr(js, "\"api_version\":\"1.3.243\"") != NULL);
    EXPECT_TRUE(strstr(js, "\"device_name\":\"Maloon \\\"L\\\"900\"") != NULL);
    EXPECT_TRUE(strstr(js, "\"VK_FORMAT_BC1_RGB_UNORM_BLOCK\":true") != NULL);
    EXPECT_TRUE(strstr(js, "\"VK_FORMAT_BC1_RGB_SRGB_BLOCK\":false") != NULL);
    EXPECT_TRUE(strstr(js, "\"cntfrq_hz\":19200000") != NULL);
    EXPECT_TRUE(strstr(js, "\"tpidrro_value_note\":\"nonzero\"") != NULL);
    free(js);
}

/* ---------- 4. cntpct 期望值数学 ---------- */
TEST(cntpct_judge_math)
{
    /* freq=24MHz，计时 10ms → expect=240000 tick，slack=24000 */
    EXPECT_TRUE(probe_cntpct_judge(24000000ULL, 1000000ULL, 1000000ULL + 240000ULL, 10));
    EXPECT_TRUE(probe_cntpct_judge(24000000ULL, 1000000ULL,
                                   1000000ULL + 240000ULL + 24000ULL, 10));  /* +10% 界内 */
    EXPECT_TRUE(probe_cntpct_judge(24000000ULL, 1000000ULL,
                                   1000000ULL + 240000ULL - 24000ULL, 10));  /* -10% 界内 */
    EXPECT_FALSE(probe_cntpct_judge(24000000ULL, 1000000ULL,
                                    1000000ULL + 240000ULL + 24001ULL, 10)); /* 超容差 */
    EXPECT_FALSE(probe_cntpct_judge(24000000ULL, 1000000ULL,
                                    1000000ULL + 240000ULL - 24001ULL, 10));
    EXPECT_FALSE(probe_cntpct_judge(24000000ULL, 5, 5, 10));      /* b==a → 单调失败 */
    EXPECT_FALSE(probe_cntpct_judge(24000000ULL, 5, 4, 10));      /* 倒退 → false */
    EXPECT_FALSE(probe_cntpct_judge(0ULL, 1, 2, 10));             /* freq=0 → false */
    /* 常见真机频率（19.2MHz）也要能算 */
    EXPECT_TRUE(probe_cntpct_judge(19200000ULL, 0, 192000ULL, 10));
}

/* ---------- 5. tpidr 哨兵数学 ---------- */
TEST(sentinel_not_trivially_true)
{
    const uint64_t cases[] = { 0, 1, 0x5555555555555555ULL, 0xDEADBEEFDEADBEEFULL,
                               0xFFFFFFFFFFFFFFFFULL };
    for (size_t i = 0; i < sizeof cases / sizeof cases[0]; i++) {
        uint64_t s = probe_sentinel(cases[i]);
        EXPECT_NE(s, cases[i]);            /* 哨兵恒 ≠ 原值（防恒真） */
        EXPECT_FALSE(probe_tpidr_writable_check(cases[i], cases[i]));  /* 写被丢弃 */
        EXPECT_TRUE(probe_tpidr_writable_check(cases[i], s));          /* 写生效 */
    }
    EXPECT_EQ(probe_sentinel(0x5555555555555555ULL), 0ULL);  /* 掩码自反点 */
}

/* ---------- 6. futex + W^X 运行时测试（仅 Linux；Windows 编译为空） ---------- */
#if defined(__linux__)
#include <errno.h>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>

TEST(runtime_futex_wait_wake)
{
    int f = 0;
    struct timespec dl = { .tv_sec = 0, .tv_nsec = 200 * 1000 * 1000 };
    /* 无人唤醒：FUTEX_WAIT 超时（证明等待语义生效） */
    long rc = syscall(SYS_futex, &f, 0 /*FUTEX_WAIT*/, 0, &dl, NULL, 0);
    EXPECT_EQ(rc, -1);
    EXPECT_EQ(errno, ETIMEDOUT);
    /* 置值后 WAKE 不阻塞（无等待者返回 0） */
    f = 1;
    EXPECT_EQ(syscall(SYS_futex, &f, 1 /*FUTEX_WAKE*/, 1, NULL, NULL, 0), 0);
}

TEST(runtime_wx_page_exec)
{
#if defined(__aarch64__)
    static const unsigned char bin[16] = {
        0x00, 0x00, 0x80, 0x52, 0xC0, 0x03, 0x5F, 0xD6,
        0, 0, 0, 0, 0, 0, 0, 0 };          /* mov w0,#0; ret */
#elif defined(__x86_64__)
    static const unsigned char bin[16] = {
        0x31, 0xC0, 0xC3, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0 }; /* xor eax,eax; ret */
#else
    EXPECT_TRUE(!"no machine code for this arch");
    return;
#endif
    void *code = mmap(NULL, sizeof bin, PROT_READ | PROT_WRITE,
                      MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    EXPECT_NE(code, MAP_FAILED);
    if (code == MAP_FAILED)
        return;
    memcpy(code, bin, sizeof bin);
    __builtin___clear_cache((char *)code, (char *)code + sizeof bin);
    EXPECT_EQ(mprotect(code, sizeof bin, PROT_READ | PROT_EXEC), 0);
    int (*fn)(void) = (int (*)(void))code;
    EXPECT_EQ(fn(), 0);
    munmap(code, sizeof bin);
}
#endif /* __linux__ */

DMTEST_MAIN()
