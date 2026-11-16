// Stage-3 probe: does the platform actually follow a requested frame rate?
// NativeDisplaySoloist and NativeVsync both take an "expected frame rate range"; this measures what the
// compositor really delivers for 30 / 45 / 60 / 90 / 120 and reports the target timestamp it hands back.
#include <napi/native_api.h>
#include <dlfcn.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

static char g_out[32768];
static size_t g_len;
static void P(const char *fmt, ...) {
    va_list ap; va_start(ap, fmt);
    int n = vsnprintf(g_out + g_len, sizeof g_out - g_len, fmt, ap);
    va_end(ap);
    if (n > 0) g_len += (size_t)n < sizeof g_out - g_len ? (size_t)n : sizeof g_out - g_len - 1;
}
static void *L(const char *lib) { return dlopen(lib, RTLD_NOW); }
static void *S(void *h, const char *n) { return h ? dlsym(h, n) : NULL; }

typedef struct OH_DisplaySoloist OH_DisplaySoloist;
typedef void (*ds_cb)(long long, long long, void *);
typedef OH_DisplaySoloist *(*fn_create)(bool);
typedef int32_t (*fn_start)(OH_DisplaySoloist *, ds_cb, void *);
typedef int32_t (*fn_stop)(OH_DisplaySoloist *);
typedef struct { int32_t min, max, expected; } range_t;
typedef int32_t (*fn_rate)(OH_DisplaySoloist *, range_t *);
typedef int32_t (*fn_destroy)(OH_DisplaySoloist *);

#define CAP 400
static volatile int g_n; static long long g_ts[CAP], g_tg[CAP];
static void on_frame(long long ts, long long tg, void *d) {
    (void)d; int n = g_n; if (n < CAP) { g_ts[n] = ts; g_tg[n] = tg; } g_n = n + 1;
}

// measure one requested rate; returns the measured Hz
static double measure(fn_create cr, fn_start st, fn_stop sp, fn_rate rt, fn_destroy de, int want, int ms) {
    OH_DisplaySoloist *d = cr(true);
    if (!d) { P("  create failed\n"); return -1; }
    if (rt) { range_t r = {want, want, want}; rt(d, &r); }
    g_n = 0;
    if (st(d, on_frame, NULL)) { P("  start failed\n"); if (de) de(d); return -1; }
    usleep((useconds_t)ms * 1000);
    if (sp) sp(d);
    int n = g_n < CAP ? g_n : CAP;
    double hz = -1;
    if (n >= 4) {
        long long a = g_ts[1], b = g_ts[n - 1];
        hz = (double)(n - 2) * 1e9 / (double)(b - a);
        double tgap = (double)(g_tg[n / 2] - g_ts[n / 2]) / 1e6;
        double first_gap = (double)(g_ts[1] - g_ts[0]) / 1e6;
        P("  want %d -> %d frames in %d ms = %.1f Hz (first gap %.2f ms, target-now %.2f ms)\n",
          want, n, ms, hz, first_gap, tgap);
    } else P("  want %d -> only %d frames\n", want, n);
    if (de) de(d);
    return hz;
}

static void run_all(void) {
    void *h = L("libnative_display_soloist.so");
    fn_create cr = (fn_create)S(h, "OH_DisplaySoloist_Create");
    fn_start st = (fn_start)S(h, "OH_DisplaySoloist_Start");
    fn_stop sp = (fn_stop)S(h, "OH_DisplaySoloist_Stop");
    fn_rate rt = (fn_rate)S(h, "OH_DisplaySoloist_SetExpectedFrameRateRange");
    fn_destroy de = (fn_destroy)S(h, "OH_DisplaySoloist_Destroy");
    P("== stage-3: does the platform follow a requested frame rate? ==\n");
    if (!cr || !st) { P("soloist unavailable\n"); return; }
    // No rate set at all: what the compositor picks on its own
    OH_DisplaySoloist *d = cr(true);
    if (d) { g_n = 0; st(d, on_frame, NULL); usleep(500 * 1000); if (sp) sp(d);
        int n = g_n < CAP ? g_n : CAP;
        if (n >= 4) P("  no rate requested -> %.1f Hz\n", (double)(n - 2) * 1e9 / (double)(g_ts[n - 1] - g_ts[1]));
        if (de) de(d); }
    int wants[] = {30, 45, 60, 90, 120};
    for (unsigned i = 0; i < sizeof wants / sizeof *wants; i++) measure(cr, st, sp, rt, de, wants[i], 700);
    P("== end ==\n");
}

static napi_value js_run(napi_env env, napi_callback_info info) {
    size_t argc = 1; napi_value argv[1] = {0}; napi_get_cb_info(env, info, &argc, argv, NULL, NULL);
    char dir[1024] = {0};
    if (argc >= 1) { size_t n = 0; napi_get_value_string_utf8(env, argv[0], dir, sizeof dir, &n); }
    g_len = 0; run_all();
    if (dir[0]) { char p[1100]; snprintf(p, sizeof p, "%s/probe3.txt", dir); FILE *f = fopen(p, "w"); if (f) { fwrite(g_out, 1, g_len, f); fclose(f); } }
    napi_value s = NULL; napi_create_string_utf8(env, g_out, g_len, &s); return s;
}
EXTERN_C_START
static napi_value init(napi_env env, napi_value exports) {
    napi_value fn = NULL;
    napi_create_function(env, "run3", NAPI_AUTO_LENGTH, js_run, NULL, &fn);
    napi_set_named_property(env, exports, "run3", fn);
    return exports;
}
EXTERN_C_END
NAPI_MODULE(ohosprobe3, init)
