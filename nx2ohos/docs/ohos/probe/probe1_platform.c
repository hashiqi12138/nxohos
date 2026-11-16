// NAPI wrapper around the platform-API probe: runs the checks on a worker thread and returns a text report,
// so a HAP can show what this device really offers a native renderer (exec from /data/local/tmp is refused here).
#include <napi/native_api.h>
#include <dlfcn.h>
#include <stdarg.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

static char g_out[16384];
static size_t g_len;
static void P(const char *fmt, ...) {
    va_list ap; va_start(ap, fmt);
    int n = vsnprintf(g_out + g_len, sizeof g_out - g_len, fmt, ap);
    va_end(ap);
    if (n > 0) g_len += (size_t)n < sizeof g_out - g_len ? (size_t)n : sizeof g_out - g_len - 1;
}
static void *sym(const char *lib, const char *name, void **h) {
    if (!*h) { *h = dlopen(lib, RTLD_NOW); if (!*h) { P("dlopen %s: %s\n", lib, dlerror()); return NULL; } }
    return dlsym(*h, name);
}

// ---- QoS --------------------------------------------------------------------------------------------------------
typedef int (*fn_set_qos)(int); typedef int (*fn_get_qos)(int *); typedef int (*fn_reset_qos)(void);
static void probe_qos(void) {
    void *h = NULL;
    fn_set_qos set = (fn_set_qos)sym("libqos.so", "OH_QoS_SetThreadQoS", &h);
    fn_get_qos get = (fn_get_qos)sym("libqos.so", "OH_QoS_GetThreadQoS", &h);
    fn_reset_qos rst = (fn_reset_qos)sym("libqos.so", "OH_QoS_ResetThreadQoS", &h);
    if (!set || !get) { P("qos: MISSING (set=%p get=%p)\n", (void *)set, (void *)get); return; }
    for (int lv = 0; lv <= 5; lv++) { int back = -1; int rc = set(lv); int rg = get(&back); P("qos: set(%d)=%d get rc=%d -> %d\n", lv, rc, rg, back); }
    if (rst) P("qos: reset=%d\n", rst());
}

// ---- NativeDisplaySoloist ----------------------------------------------------------------------------------------
typedef struct OH_DisplaySoloist OH_DisplaySoloist;
typedef void (*ds_cb)(long long, long long, void *);
typedef OH_DisplaySoloist *(*fn_ds_create)(bool);
typedef int32_t (*fn_ds_start)(OH_DisplaySoloist *, ds_cb, void *);
typedef int32_t (*fn_ds_stop)(OH_DisplaySoloist *);
typedef struct { int32_t min, max, expected; } ds_range;
typedef int32_t (*fn_ds_rate)(OH_DisplaySoloist *, ds_range *);
typedef int32_t (*fn_ds_destroy)(OH_DisplaySoloist *);
static volatile int g_ds_n; static long long g_ds_ts[64];
static void ds_on_frame(long long ts, long long tg, void *d) { (void)tg; (void)d; int n = g_ds_n; if (n < 64) g_ds_ts[n] = ts; g_ds_n = n + 1; }
static void probe_soloist(void) {
    void *h = NULL;
    fn_ds_create cr = (fn_ds_create)sym("libnative_display_soloist.so", "OH_DisplaySoloist_Create", &h);
    fn_ds_start st = (fn_ds_start)sym("libnative_display_soloist.so", "OH_DisplaySoloist_Start", &h);
    fn_ds_stop sp = (fn_ds_stop)sym("libnative_display_soloist.so", "OH_DisplaySoloist_Stop", &h);
    fn_ds_rate rt = (fn_ds_rate)sym("libnative_display_soloist.so", "OH_DisplaySoloist_SetExpectedFrameRateRange", &h);
    fn_ds_destroy de = (fn_ds_destroy)sym("libnative_display_soloist.so", "OH_DisplaySoloist_Destroy", &h);
    if (!cr || !st) { P("soloist: MISSING\n"); return; }
    OH_DisplaySoloist *d = cr(true);
    P("soloist: create(exclusive)=%p\n", (void *)d);
    if (!d) return;
    if (rt) { ds_range r = {30, 120, 120}; P("soloist: setRate(30..120 exp120)=%d\n", rt(d, &r)); }
    P("soloist: start=%d\n", st(d, ds_on_frame, NULL));
    usleep(500 * 1000);
    if (sp) sp(d);
    int n = g_ds_n; P("soloist: callbacks in 500 ms = %d\n", n);
    if (n >= 3) { double sum = 0; for (int i = 1; i < n && i < 60; i++) sum += (double)(g_ds_ts[i] - g_ds_ts[i - 1]) / 1e6;
        P("soloist: mean gap = %.3f ms over %d gaps -> %.1f Hz\n", sum / (n - 1), n - 1, 1000.0 / (sum / (n - 1))); }
    if (de) de(d);
}

// ---- NativeVsync -------------------------------------------------------------------------------------------------
typedef struct OH_NativeVSync OH_NativeVSync;
typedef void (*vs_cb)(long long, void *);
typedef OH_NativeVSync *(*fn_vs_create)(const char *, unsigned int);
typedef int (*fn_vs_req)(OH_NativeVSync *, vs_cb, void *);
typedef int (*fn_vs_period)(OH_NativeVSync *, long long *);
typedef int (*fn_vs_dv)(OH_NativeVSync *, bool);
typedef struct { int32_t min, max, expected; } vs_range;
typedef int (*fn_vs_rate)(OH_NativeVSync *, vs_range *);
static volatile int g_vs_n; static long long g_vs_ts[64];
static void vs_on_frame(long long ts, void *d) { (void)d; int n = g_vs_n; if (n < 64) g_vs_ts[n] = ts; g_vs_n = n + 1; }
static void probe_vsync(void) {
    void *h = NULL;
    fn_vs_create cr = (fn_vs_create)sym("libnative_vsync.so", "OH_NativeVSync_Create", &h);
    fn_vs_req rq = (fn_vs_req)sym("libnative_vsync.so", "OH_NativeVSync_RequestFrame", &h);
    fn_vs_period pe = (fn_vs_period)sym("libnative_vsync.so", "OH_NativeVSync_GetPeriod", &h);
    fn_vs_dv dv = (fn_vs_dv)sym("libnative_vsync.so", "OH_NativeVSync_DVSyncSwitch", &h);
    fn_vs_rate ra = (fn_vs_rate)sym("libnative_vsync.so", "OH_NativeVSync_SetExpectedFrameRateRange", &h);
    void *cw = sym("libnative_vsync.so", "OH_NativeVSync_Create_ForAssociatedWindow", &h);
    if (!cr || !rq) { P("vsync: MISSING\n"); return; }
    OH_NativeVSync *v = cr("dmprobe", 7);
    P("vsync: create=%p Create_ForAssociatedWindow=%s\n", (void *)v, cw ? "present" : "ABSENT");
    if (!v) return;
    rq(v, vs_on_frame, NULL);
    usleep(100 * 1000);
    long long per = 0;
    if (pe) P("vsync: getPeriod rc=%d period=%.3f ms\n", pe(v, &per), (double)per / 1e6);
    if (ra) { vs_range r = {30, 120, 120}; P("vsync: setRate(30..120 exp120)=%d\n", ra(v, &r)); }
    if (dv) P("vsync: DVSyncSwitch(true)=%d\n", dv(v, true));
    int b = g_vs_n; usleep(300 * 1000);
    int n = g_vs_n; P("vsync: callbacks in 300 ms = %d\n", n - b);
    if (n >= 3) { double sum = 0; int k = n < 64 ? n : 64; for (int i = 1; i < k; i++) sum += (double)(g_vs_ts[i] - g_vs_ts[i - 1]) / 1e6;
        P("vsync: mean gap = %.3f ms -> %.1f Hz\n", sum / (k - 1), 1000.0 / (sum / (k - 1))); }
    if (dv) dv(v, false);
}

// ---- OpenGTX -----------------------------------------------------------------------------------------------------
typedef struct OpenGTX_Context OpenGTX_Context;
typedef OpenGTX_Context *(*fn_gtx_create)(void *);
typedef int (*fn_gtx_cfg)(OpenGTX_Context *, const void *);
typedef int (*fn_gtx_act)(OpenGTX_Context *);
typedef int (*fn_gtx_destroy)(OpenGTX_Context **);
static void probe_opengtx(void) {
    void *h = NULL;
    fn_gtx_create cr = (fn_gtx_create)sym("libopengtx.so", "HMS_OpenGTX_CreateContext", &h);
    if (!cr) { P("opengtx: CreateContext MISSING\n"); return; }
    OpenGTX_Context *c = cr(NULL);
    P("opengtx: CreateContext(NULL)=%p%s\n", (void *)c, c ? "" : "  (nullptr)");
    if (!c) return;
    fn_gtx_cfg cfg = (fn_gtx_cfg)sym("libopengtx.so", "HMS_OpenGTX_SetConfiguration", &h);
    fn_gtx_act ac = (fn_gtx_act)sym("libopengtx.so", "HMS_OpenGTX_Activate", &h);
    fn_gtx_destroy de = (fn_gtx_destroy)sym("libopengtx.so", "HMS_OpenGTX_DestroyContext", &h);
    if (cfg) P("opengtx: SetConfiguration=%p (needs a full OpenGTX_ConfigDescription)\n", (void *)cfg);
    if (ac) P("opengtx: Activate (unconfigured) = %d\n", ac(c));
    if (de) P("opengtx: Destroy = %d\n", de(&c));
}

// ---- census ------------------------------------------------------------------------------------------------------
static void census(const char *lib, const char *const *names, int n) {
    void *h = dlopen(lib, RTLD_NOW);
    if (!h) { P("census %s: %s\n", lib, dlerror()); return; }
    P("census %s:", lib);
    for (int i = 0; i < n; i++) P(" %s=%s", names[i], dlsym(h, names[i]) ? "y" : "-");
    P("\n");
}

static void run_all(void) {
    P("== platform API probe (pid %d) ==\n", (int)getpid());
    probe_qos(); probe_vsync(); probe_soloist(); probe_opengtx();
    static const char *const gp[] = {"HMS_GamePerformance_Init", "HMS_GamePerformance_QueryGpuInfo", "HMS_GamePerformance_QueryCpuInfo",
        "HMS_GamePerformance_QueryThermalInfo", "HMS_GamePerformance_RegisterThermalLevelChangedCallback"};
    census("libgame_performance.z.so", gp, 5);
    static const char *const xeg[] = {"HMS_XEG_EnumerateDeviceExtensionProperties", "HMS_XEG_CreateTemporalUpscale",
        "HMS_XEG_CmdRenderTemporalUpscale", "HMS_XEG_CreateSpatialUpscale", "HMS_XEG_CreateAdaptiveVRS", "HMS_XEG_CmdDispatchAdaptiveVRS"};
    census("libxengine.so", xeg, 6);
    static const char *const fg[] = {"HMS_FG_CreateContext_VK", "HMS_FG_CreateImage_VK", "HMS_FG_Dispatch_VK"};
    census("libframegeneration.so", fg, 3);
    static const char *const abr[] = {"HMS_ABR_CreateContext", "HMS_ABR_GetNextScale"};
    census("libabr.so", abr, 2);
    P("== end ==\n");
}

// ---- NAPI --------------------------------------------------------------------------------------------------------
static napi_value js_run(napi_env env, napi_callback_info info) {
    size_t argc = 1; napi_value argv[1] = {0}; napi_get_cb_info(env, info, &argc, argv, NULL, NULL);
    char dir[1024] = {0};
    if (argc >= 1) { size_t n = 0; napi_get_value_string_utf8(env, argv[0], dir, sizeof dir, &n); }
    g_len = 0; run_all();
    if (dir[0]) {
        char p[1100]; snprintf(p, sizeof p, "%s/probe.txt", dir);
        FILE *f = fopen(p, "w"); if (f) { fwrite(g_out, 1, g_len, f); fclose(f); }
    }
    napi_value s = NULL; napi_create_string_utf8(env, g_out, g_len, &s);
    return s ? s : NULL;
}
EXTERN_C_START
static napi_value init(napi_env env, napi_value exports) {
    napi_value fn = NULL;
    napi_create_function(env, "run", NAPI_AUTO_LENGTH, js_run, NULL, &fn);
    napi_set_named_property(env, exports, "run", fn);
    return exports;
}
EXTERN_C_END
NAPI_MODULE(ohosprobe, init)
