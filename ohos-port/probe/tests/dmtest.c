/* dmtest.c — dmtest.h 断言头的参考实现（注册表 + 运行器）
 *
 * 用法：把本文件与被测 TU 一起编译链接，被测 TU 尾部提供
 *   int main(void){ return dmtest_run_all(); }   （或直接用 DMTEST_MAIN()）
 * 返回值 = 失败用例数（0 = 全绿）。
 */
#include "dmtest.h"

#include <stdarg.h>

#define DMTEST_MAX_CASES 1024

static dmtest_fn        g_case[DMTEST_MAX_CASES];
static const char      *g_name[DMTEST_MAX_CASES];
static int              g_count;
static dmtest_reset_fn  g_reset;
static int g_case_fails;      /* 当前用例内失败断言数 */
static int g_total_fails;     /* 全程失败断言数 */
static int g_failed_cases;    /* 失败用例数 */

void dmtest_register(const char *name, dmtest_fn fn)
{
    for (int i = 0; i < g_count; i++) {
        if (strcmp(g_name[i], name) == 0) {   /* 重名：覆盖旧槽位并告警，
                                               * 避免同名用例跑两遍/静默漏测 */
            fprintf(stderr, "dmtest: duplicate TEST name '%s' (overridden)\n", name);
            g_name[i] = name;
            g_case[i] = fn;
            return;
        }
    }
    if (g_count >= DMTEST_MAX_CASES) {
        fprintf(stderr, "dmtest: case table full (%d), dropping '%s'\n",
                DMTEST_MAX_CASES, name);
        return;
    }
    g_name[g_count] = name;
    g_case[g_count] = fn;
    g_count++;
}

void dmtest_set_reset(dmtest_reset_fn fn)
{
    g_reset = fn;
}

int dmtest_failures(void)
{
    return g_case_fails;
}

void dmtest_fail(const char *file, int line, const char *fmt, ...)
{
    va_list ap;
    g_case_fails++;
    g_total_fails++;
    fprintf(stderr, "    FAIL %s:%d: ", file, line);
    va_start(ap, fmt);
    vfprintf(stderr, fmt, ap);
    va_end(ap);
    fputc('\n', stderr);
}

int dmtest_run_all(void)
{
    printf("dmtest: %d case(s)\n", g_count);
    for (int i = 0; i < g_count; i++) {
        g_case_fails = 0;               /* 每个用例入口清零 → REQUIRE 语义正确 */
        if (g_reset)
            g_reset();
        printf("[ RUN  ] %s\n", g_name[i]);
        g_case[i]();
        if (g_case_fails) {
            g_failed_cases++;
            printf("[ FAIL ] %s (%d assertion(s))\n", g_name[i], g_case_fails);
        } else {
            printf("[  OK  ] %s\n", g_name[i]);
        }
    }
    printf("dmtest: %d/%d passed, %d failed case(s), %d failed assertion(s)\n",
           g_count - g_failed_cases, g_count, g_failed_cases, g_total_fails);
    return g_failed_cases;
}
