// The HAP's native module (libdmhost.so) and the only place ArkTS talks to the game host: the XComponent's surface, the start call,
// and (later) touch and lifecycle. Everything else lives in the host itself (dm/host, dm/gfx) and in front_ohos.c.
#include <napi/native_api.h>
#include <pthread.h>
#include <stdarg.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

void dm_ohos_front_set_surface(uint64_t surface_id, int w, int h);
void dm_ohos_front_surface_lost(void);
void dm_ohos_game_start(const char *root, const char *save, const char *temp);
void dm_ohos_demo_start(void);
void dm_ohos_demo_stop(void);
static void dm_demo_start(void) { dm_ohos_demo_start(); }
static void dm_demo_stop(void) { dm_ohos_demo_stop(); }

static void logi(const char *m, ...);

static int arg_str(napi_env env, napi_value v, char *out, size_t cap) {
    size_t n = 0;
    if (!v || napi_get_value_string_utf8(env, v, out, cap, &n) != napi_ok) { if (cap) out[0] = 0; return -1; }
    return 0;
}
static long long arg_num(napi_env env, napi_value v) {
    double d = 0; if (v) napi_get_value_double(env, v, &d); return (long long)d;
}

// setSurfaceId(surfaceId: string, width: number, height: number): the XComponent's onLoad / onAreaChange
static napi_value js_set_surface(napi_env env, napi_callback_info info) {
    napi_value argv[3] = {0}; size_t argc = 3, n = 0; napi_get_cb_info(env, info, &argc, argv, NULL, NULL);
    napi_value undef; napi_get_undefined(env, &undef);
    char id[32] = {0};
    if (arg_str(env, argv[0], id, sizeof id)) { logi("napi: setSurfaceId without a surface id"); return undef; }
    dm_ohos_front_set_surface(strtoull(id, NULL, 10), (int)arg_num(env, argv[1]), (int)arg_num(env, argv[2]));
    return undef;
}
static napi_value js_surface_lost(napi_env env, napi_callback_info info) {
    (void)info; napi_value undef; napi_get_undefined(env, &undef); dm_ohos_front_surface_lost(); return undef;
}

static char g_root[1024], g_save[1024], g_temp[1024];
static _Atomic int g_game_started;   // dm_main runs the guest once per process: a second start would run two guests on one window
static void *start_trampoline(void *unused) { (void)unused; dm_ohos_game_start(g_root, g_save, g_temp); return NULL; }
// startGame(root: string, save: string, temp: string): the sandbox's files dir, save dir and temp dir; returns at once
static napi_value js_start(napi_env env, napi_callback_info info) {
    napi_value argv[3] = {0}; size_t argc = 3; napi_get_cb_info(env, info, &argc, argv, NULL, NULL);
    napi_value undef; napi_get_undefined(env, &undef);
    if (argc < 3 || arg_str(env, argv[0], g_root, sizeof g_root) || arg_str(env, argv[1], g_save, sizeof g_save) || arg_str(env, argv[2], g_temp, sizeof g_temp)) {
        logi("napi: startGame needs (root, save, temp)"); return undef;
    }
    if (atomic_exchange(&g_game_started, 1)) { logi("napi: startGame ignored: the guest is already running"); return undef; }
    pthread_t th; pthread_attr_t at; pthread_attr_init(&at);
    pthread_attr_setstacksize(&at, 8 << 20);   // the guest runs on this thread: give it room like the Mac front end's 8 MB
    if (pthread_create(&th, &at, start_trampoline, NULL)) { atomic_store(&g_game_started, 0); logi("napi: cannot start the game thread"); }
    else { pthread_detach(th); logi("napi: game thread started (root=%s)", g_root); }
    pthread_attr_destroy(&at);
    return undef;
}

static napi_value js_demo(napi_env env, napi_callback_info info) {
    (void)info; napi_value undef; napi_get_undefined(env, &undef); dm_demo_start(); return undef;
}
static napi_value js_demo_stop(napi_env env, napi_callback_info info) {
    (void)info; napi_value undef; napi_get_undefined(env, &undef); dm_demo_stop(); return undef;
}

// log(msg: string): the page's progress in the same hilog stream as the native module. console.info does not reach it under a tag that
// `hilog -T` can select here (measured), so the page reports through this instead.
static napi_value js_log(napi_env env, napi_callback_info info) {
    napi_value argv[1] = {0}; size_t argc = 1; napi_get_cb_info(env, info, &argc, argv, NULL, NULL);
    napi_value undef; napi_get_undefined(env, &undef);
    char m[1024] = {0};
    if (!arg_str(env, argv[0], m, sizeof m)) logi("page: %s", m);
    return undef;
}

void dm_ohos_logv(const char *fmt, ...);   // ohos_log.c: hilog with a domain and tag that survive the console's log export
static void logi(const char *m, ...) {
    va_list ap; va_start(ap, m); char b[1024]; vsnprintf(b, sizeof b, m, ap); va_end(ap);
    dm_ohos_logv("%s", b);
}

EXTERN_C_START
static napi_value dmhost_init(napi_env env, napi_value exports) {
    struct { const char *name; napi_callback fn; } fns[] = {
        {"setSurfaceId", js_set_surface}, {"surfaceLost", js_surface_lost}, {"startGame", js_start},
        {"startDemo", js_demo}, {"stopDemo", js_demo_stop}, {"log", js_log},
    };
    for (size_t i = 0; i < sizeof fns / sizeof fns[0]; i++) {
        napi_value fn = NULL;
        // this SDK's napi_property_descriptor.name is a napi_value, so create the function and set it by name
        if (napi_create_function(env, fns[i].name, NAPI_AUTO_LENGTH, fns[i].fn, NULL, &fn) == napi_ok && fn)
            napi_set_named_property(env, exports, fns[i].name, fn);
        else logi("napi: export %s failed", fns[i].name);
    }
    logi("napi: libdmhost loaded");
    return exports;
}
EXTERN_C_END

// the module name must match the .so (libdmhost.so) and the ArkTS `import ... from 'libdmhost.so'`
NAPI_MODULE(dmhost, dmhost_init)
