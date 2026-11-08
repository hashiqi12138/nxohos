/* dmtest.h — 轻量断言测试头（B 文档 §依赖4：所有 TEST/EXPECT_* 宏统一定义于此）
 *
 * 设计约束：
 *  - 零第三方依赖，风格与上游 dm/host/tests 一致；
 *  - TEST(fn) 通过链接期注册表自动收集，RUN_ALL 前（每个用例入口）调用
 *    dmtest 全局 reset 钩子（dmtest_set_reset 注册，满足"每个 TEST 入口自动
 *    reset 全局状态"的契约）；
 *  - EXPECT_* 失败不中止当前用例（计数），REQUIRE_* 失败立即 return；
 *  - dmtest_run_all() 返回失败用例数（0 = 全绿）。
 */
#ifndef DMTEST_H
#define DMTEST_H

#include <stddef.h>
#include <stdio.h>
#include <string.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef void (*dmtest_fn)(void);
typedef void (*dmtest_reset_fn)(void);

void dmtest_register(const char *name, dmtest_fn fn);
void dmtest_set_reset(dmtest_reset_fn fn);   /* 每个用例执行前调用一次 */
int  dmtest_run_all(void);                   /* 返回失败用例数（0 = 全绿） */
int  dmtest_failures(void);                  /* 当前用例内累计断言失败数（REQUIRE 判据；
                                              * dmtest_run_all 在每个用例入口自动清零） */

void dmtest_fail(const char *file, int line, const char *fmt, ...);

/* 内部：注册段。用每个用例一个文件级静态结构 + 构造函数注册，兼容
 * clang/gcc（OHOS/mingw/linux）；不做 MSVC 支持（T0 跑在 clang/gcc 上）。 */
#define DMTEST_CAT_(a, b) a##b
#define DMTEST_CAT(a, b) DMTEST_CAT_(a, b)

#define TEST(name)                                                        \
    static void DMTEST_CAT(dmtest_body_, name)(void);                     \
    __attribute__((constructor)) static void DMTEST_CAT(dmtest_reg_, name)(void) { \
        dmtest_register(#name, DMTEST_CAT(dmtest_body_, name));           \
    }                                                                     \
    static void DMTEST_CAT(dmtest_body_, name)(void)

#define DMTEST_FAIL_(fmt, ...) \
    dmtest_fail(__FILE__, __LINE__, fmt, ##__VA_ARGS__)

#define EXPECT_EQ(a, b)  do { long long _a=(long long)(a), _b=(long long)(b); \
    if (_a != _b) DMTEST_FAIL_("EXPECT_EQ(%s,%s) got %lld want %lld", #a, #b, _a, _b); } while (0)
#define EXPECT_NE(a, b)  do { long long _a=(long long)(a), _b=(long long)(b); \
    if (_a == _b) DMTEST_FAIL_("EXPECT_NE(%s,%s) both %lld", #a, #b, _a); } while (0)
#define EXPECT_GT(a, b)  do { long long _a=(long long)(a), _b=(long long)(b); \
    if (!(_a > _b)) DMTEST_FAIL_("EXPECT_GT(%s,%s) got %lld not > %lld", #a, #b, _a, _b); } while (0)
#define EXPECT_GE(a, b)  do { long long _a=(long long)(a), _b=(long long)(b); \
    if (!(_a >= _b)) DMTEST_FAIL_("EXPECT_GE(%s,%s) got %lld not >= %lld", #a, #b, _a, _b); } while (0)
#define EXPECT_LT(a, b)  do { long long _a=(long long)(a), _b=(long long)(b); \
    if (!(_a < _b)) DMTEST_FAIL_("EXPECT_LT(%s,%s) got %lld not < %lld", #a, #b, _a, _b); } while (0)
#define EXPECT_LE(a, b)  do { long long _a=(long long)(a), _b=(long long)(b); \
    if (!(_a <= _b)) DMTEST_FAIL_("EXPECT_LE(%s,%s) got %lld not <= %lld", #a, #b, _a, _b); } while (0)
#define EXPECT_TRUE(c)   do { int _c=!!(c); \
    if (!_c) DMTEST_FAIL_("EXPECT_TRUE(%s) is false", #c); } while (0)
#define EXPECT_FALSE(c)  do { int _c=!!(c); \
    if (_c) DMTEST_FAIL_("EXPECT_FALSE(%s) is true", #c); } while (0)
#define EXPECT_STREQ(a, b) do { const char *_a=(a), *_b=(b); \
    if (strcmp(_a, _b) != 0) DMTEST_FAIL_("EXPECT_STREQ(%s,%s) got \"%s\" want \"%s\"", #a, #b, _a, _b); } while (0)
#define EXPECT_NULL(p)   do { const void *_p=(p); \
    if (_p) DMTEST_FAIL_("EXPECT_NULL(%s) is %p", #p, _p); } while (0)
#define EXPECT_NOT_NULL(p) do { const void *_p=(p); \
    if (!_p) DMTEST_FAIL_("EXPECT_NOT_NULL(%s)", #p); } while (0)

#define REQUIRE_EQ(a, b)  do { EXPECT_EQ(a, b); if (dmtest_failures()) return; } while (0)
#define REQUIRE_TRUE(c)   do { EXPECT_TRUE(c); if (dmtest_failures()) return; } while (0)

/* 用例实现只需在文件尾部提供 main： int main(void){ return dmtest_run_all(); } */
#define DMTEST_MAIN()                                                    \
    int main(void) { return dmtest_run_all(); }

#ifdef __cplusplus
}
#endif

#endif /* DMTEST_H */
