// Second-stage probe: the parts that need a real Vulkan device and a real app identity.
//   - XEngine: enumerate the XEG_* Vulkan extensions the Maleoon driver + libxengine expose here
//   - OpenGTX: configure + activate LTPO with this app's own package name
//   - GamePerformance: init with this bundle, then read temp level / GPU load / thermal trend
//   - ABR: create a context and read the scale the platform suggests
//   - Frame Generation: create a VK context from the real VkDevice (dispatch needs real images, not attempted)
#include <napi/native_api.h>
#include <dlfcn.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <vulkan/vulkan.h>

static char g_out[32768];
static size_t g_len;
static void P(const char *fmt, ...) {
    va_list ap; va_start(ap, fmt);
    int n = vsnprintf(g_out + g_len, sizeof g_out - g_len, fmt, ap);
    va_end(ap);
    if (n > 0) g_len += (size_t)n < sizeof g_out - g_len ? (size_t)n : sizeof g_out - g_len - 1;
}
static void *L(const char *lib) { void *h = dlopen(lib, RTLD_NOW); if (!h) P("dlopen %s: %s\n", lib, dlerror()); return h; }
static void *S(void *h, const char *n) { return h ? dlsym(h, n) : NULL; }

// ---- Vulkan device (headless instance, first physical device, first graphics queue) ------------------------------
static VkInstance g_inst; static VkPhysicalDevice g_pd; static VkDevice g_dev; static VkQueue g_q; static uint32_t g_qf;
static int vk_open(void) {
    VkApplicationInfo ai = {VK_STRUCTURE_TYPE_APPLICATION_INFO, NULL, "dmprobe", 1, NULL, 0, VK_API_VERSION_1_1};
    VkInstanceCreateInfo ci = {VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, NULL, 0, &ai, 0, NULL, 0, NULL};
    VkResult r = vkCreateInstance(&ci, NULL, &g_inst);
    if (r) { P("vk: createInstance failed %d\n", r); return 0; }
    uint32_t n = 0; vkEnumeratePhysicalDevices(g_inst, &n, NULL);
    if (!n) { P("vk: no physical device\n"); return 0; }
    VkPhysicalDevice pd[8]; if (n > 8) n = 8; vkEnumeratePhysicalDevices(g_inst, &n, pd); g_pd = pd[0];
    VkPhysicalDeviceProperties pp; vkGetPhysicalDeviceProperties(g_pd, &pp);
    P("vk: device '%s' api %u.%u.%u driver %u type %d\n", pp.deviceName, VK_VERSION_MAJOR(pp.apiVersion),
      VK_VERSION_MINOR(pp.apiVersion), VK_VERSION_PATCH(pp.apiVersion), pp.driverVersion, pp.deviceType);
    uint32_t nq = 0; vkGetPhysicalDeviceQueueFamilyProperties(g_pd, &nq, NULL);
    VkQueueFamilyProperties qf[16]; if (nq > 16) nq = 16; vkGetPhysicalDeviceQueueFamilyProperties(g_pd, &nq, qf);
    int found = 0; for (uint32_t i = 0; i < nq; i++) if ((qf[i].queueFlags & VK_QUEUE_GRAPHICS_BIT) && !found) { g_qf = i; found = 1; }
    if (!found) { P("vk: no graphics queue\n"); return 0; }
    float prio = 1.0f;
    VkDeviceQueueCreateInfo qi = {VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO, NULL, 0, g_qf, 1, &prio};
    VkDeviceCreateInfo di = {VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO, NULL, 0, 1, &qi, 0, NULL, 0, NULL, NULL};
    r = vkCreateDevice(g_pd, &di, NULL, &g_dev);
    if (r) { P("vk: createDevice failed %d\n", r); return 0; }
    vkGetDeviceQueue(g_dev, g_qf, 0, &g_q);
    P("vk: device ready (queue family %u)\n", g_qf);
    return 1;
}

// ---- XEngine extensions ------------------------------------------------------------------------------------------
typedef struct { char extensionName[256]; uint32_t version; } XEG_Ext;
typedef VkResult (*fn_xeg_enum)(VkPhysicalDevice, uint32_t *, XEG_Ext *);
static void probe_xeg(void) {
    void *h = L("libxengine.so"); if (!h) return;
    fn_xeg_enum en = (fn_xeg_enum)S(h, "HMS_XEG_EnumerateDeviceExtensionProperties");
    if (!en) { P("xeg: enumerate missing\n"); return; }
    uint32_t n = 0; VkResult r = en(g_pd, &n, NULL);
    P("xeg: enumerate(count only) r=%d n=%u\n", r, n);
    if (!n) return;
    XEG_Ext ex[32]; uint32_t k = n > 32 ? 32 : n;
    r = en(g_pd, &k, ex);
    P("xeg: enumerate r=%d count=%u\n", r, k);
    for (uint32_t i = 0; i < k; i++) P("xeg:   %s v%u\n", ex[i].extensionName, ex[i].version);
}

// ---- OpenGTX -----------------------------------------------------------------------------------------------------
typedef struct OpenGTX_Context OpenGTX_Context;
typedef struct { int32_t height, width; } GTX_Res;
typedef struct {
    int mode; int32_t targetFPS; char *packageName; char *appVersion; int engineType; char *engineVersion;
    int gameType; int pictureQualityMaxLevel; GTX_Res resolutionMaxValue;
    int32_t gameMainThreadId, gameRenderThreadId, gameKeyThreadIds[5]; bool vulkanSupport;
} GTX_Cfg;
typedef OpenGTX_Context *(*fn_gtx_create)(void *);
typedef int (*fn_gtx_cfg)(OpenGTX_Context *, const GTX_Cfg *);
typedef int (*fn_gtx_act)(OpenGTX_Context *);
typedef int (*fn_gtx_deact)(OpenGTX_Context *);
typedef int (*fn_gtx_destroy)(OpenGTX_Context **);
typedef struct { int sceneID; char *description; int32_t recommendFPS, minFPS, maxFPS; GTX_Res resolutionCurValue; } GTX_Scene;
typedef int (*fn_gtx_scene)(OpenGTX_Context *, const GTX_Scene *);
typedef struct { float x, y, z; } GTX_V3;
typedef struct { GTX_V3 mainCameraPosition, mainCameraRotate; } GTX_Frame;
typedef int (*fn_gtx_frame)(OpenGTX_Context *, const GTX_Frame *);
static void probe_gtx(const char *bundle, int targetFps) {
    void *h = L("libopengtx.so"); if (!h) return;
    fn_gtx_create cr = (fn_gtx_create)S(h, "HMS_OpenGTX_CreateContext");
    if (!cr) { P("gtx: create missing\n"); return; }
    OpenGTX_Context *c = cr(NULL);
    P("gtx: CreateContext=%p\n", (void *)c);
    if (!c) return;
    fn_gtx_cfg cfg = (fn_gtx_cfg)S(h, "HMS_OpenGTX_SetConfiguration");
    fn_gtx_act ac = (fn_gtx_act)S(h, "HMS_OpenGTX_Activate");
    fn_gtx_deact da = (fn_gtx_deact)S(h, "HMS_OpenGTX_Deactivate");
    fn_gtx_destroy de = (fn_gtx_destroy)S(h, "HMS_OpenGTX_DestroyContext");
    GTX_Cfg g; memset(&g, 0, sizeof g);
    g.mode = 0x0100;                        // ADAPTIVE_MODE
    g.targetFPS = targetFps;
    g.packageName = (char *)bundle;
    g.appVersion = (char *)"1.0.0";
    g.engineType = 100;                     // OTHERS_ENGINE
    g.engineVersion = (char *)"nxnative";
    g.gameType = 2;                         // RPG
    g.pictureQualityMaxLevel = 3;           // FHD
    g.resolutionMaxValue.height = 1080; g.resolutionMaxValue.width = 1920;
    g.gameMainThreadId = (int32_t)getpid(); g.gameRenderThreadId = (int32_t)getpid();
    g.vulkanSupport = g_dev != VK_NULL_HANDLE;
    if (cfg) P("gtx: SetConfiguration(ADAPTIVE,target %d,vulkan %d)=%d\n", targetFps, (int)g.vulkanSupport, cfg(c, &g));
    if (ac) P("gtx: Activate=%d\n", ac(c));
    fn_gtx_scene sc = (fn_gtx_scene)S(h, "HMS_OpenGTX_DispatchGameSceneInfo");
    if (sc) { GTX_Scene s; memset(&s, 0, sizeof s); s.sceneID = 4; s.description = (char *)"playing"; s.recommendFPS = targetFps;
        s.minFPS = 30; s.maxFPS = targetFps; s.resolutionCurValue.height = 1080; s.resolutionCurValue.width = 1920;
        P("gtx: DispatchGameSceneInfo(PLAYING)=%d\n", sc(c, &s)); }
    fn_gtx_frame fr = (fn_gtx_frame)S(h, "HMS_OpenGTX_DispatchFrameRenderInfo");
    if (fr) { GTX_Frame f; memset(&f, 0, sizeof f); P("gtx: DispatchFrameRenderInfo=%d\n", fr(c, &f)); }
    if (da) P("gtx: Deactivate=%d\n", da(c));
    if (de) P("gtx: Destroy=%d\n", de(&c));
}

// ---- GamePerformance ---------------------------------------------------------------------------------------------
typedef struct GP_Init GP_Init; typedef struct GP_DevInfo GP_DevInfo; typedef struct GP_Gpu GP_Gpu; typedef struct GP_Thermal GP_Thermal;
typedef int (*fn_gp_create_init)(GP_Init **);
typedef int (*fn_gp_init_set_bundle)(GP_Init *, const char *);
typedef int (*fn_gp_init_set_ver)(GP_Init *, const char *);
typedef int (*fn_gp_init)(GP_Init *);
typedef int (*fn_gp_destroy_init)(GP_Init **);
typedef int (*fn_gp_query_gpu)(GP_Gpu **);
typedef int (*fn_gp_query_thermal)(void *, GP_Thermal **);
typedef int (*fn_gp_gpu_get)(GP_Gpu *, int32_t *);
typedef int (*fn_gp_th_get)(GP_Thermal *, int32_t *);
typedef int (*fn_gp_destroy_gpu)(GP_Gpu **);
typedef int (*fn_gp_destroy_thermal)(GP_Thermal **);
static void probe_gp(const char *bundle) {
    void *h = L("libgame_performance.z.so"); if (!h) return;
    fn_gp_create_init ci = (fn_gp_create_init)S(h, "HMS_GamePerformance_CreateInitParameters");
    fn_gp_init_set_bundle sb = (fn_gp_init_set_bundle)S(h, "HMS_GamePerformance_InitParameters_SetBundleName");
    fn_gp_init_set_ver sv = (fn_gp_init_set_ver)S(h, "HMS_GamePerformance_InitParameters_SetAppVersion");
    fn_gp_init in = (fn_gp_init)S(h, "HMS_GamePerformance_Init");
    fn_gp_destroy_init di = (fn_gp_destroy_init)S(h, "HMS_GamePerformance_DestroyInitParameters");
    if (!ci || !sb || !in) { P("gp: init symbols missing\n"); return; }
    GP_Init *p = NULL; P("gp: CreateInitParameters=%d\n", ci(&p));
    if (!p) return;
    P("gp: SetBundleName=%d SetAppVersion=%d\n", sb(p, bundle), sv ? sv(p, "1.0.0") : -1);
    P("gp: Init=%d\n", in(p));
    if (di) di(&p);
    fn_gp_query_gpu qg = (fn_gp_query_gpu)S(h, "HMS_GamePerformance_QueryGpuInfo");
    fn_gp_gpu_get gg = (fn_gp_gpu_get)S(h, "HMS_GamePerformance_GpuInfo_GetGpuLoadLevel");
    fn_gp_destroy_gpu dg = (fn_gp_destroy_gpu)S(h, "HMS_GamePerformance_DestroyGpuInfo");
    if (qg && gg) {
        GP_Gpu *g = NULL; int rc = qg(&g); int32_t lv = -1;
        P("gp: QueryGpuInfo=%d handle=%p\n", rc, (void *)g);
        if (!rc && g) { P("gp:   GpuLoadLevel rc=%d level=%d\n", gg(g, &lv), lv);
            fn_gp_gpu_get gf = (fn_gp_gpu_get)S(h, "HMS_GamePerformance_GpuInfo_GetCurrentFrequency");
            if (gf) { int32_t f = -1; P("gp:   CurrentFrequency rc=%d = %d kHz\n", gf(g, &f), f); }
            fn_gp_gpu_get gb = (fn_gp_gpu_get)S(h, "HMS_GamePerformance_GpuInfo_GetBandwidthLoadLevel");
            if (gb) { int32_t b = -1; P("gp:   BandwidthLoadLevel rc=%d = %d\n", gb(g, &b), b); }
            if (dg) dg(&g); }
    }
    fn_gp_query_thermal qt = (fn_gp_query_thermal)S(h, "HMS_GamePerformance_QueryThermalInfo");
    fn_gp_th_get tl = (fn_gp_th_get)S(h, "HMS_GamePerformance_ThermalInfo_GetThermalLevel");
    if (qt && tl) {
        GP_Thermal *t = NULL; int rc = qt(NULL, &t); int32_t lv = -1;
        P("gp: QueryThermalInfo(NULL params)=%d handle=%p\n", rc, (void *)t);
        if (!rc && t) { P("gp:   ThermalLevel rc=%d level=%d\n", tl(t, &lv), lv);
            fn_gp_th_get tt = (fn_gp_th_get)S(h, "HMS_GamePerformance_ThermalInfo_GetThermalTrend");
            if (tt) { int32_t v = -999; P("gp:   ThermalTrend rc=%d = %d\n", tt(t, &v), v); }
            fn_gp_th_get tm = (fn_gp_th_get)S(h, "HMS_GamePerformance_ThermalInfo_GetThermalMargin");
            if (tm) { int32_t v = -999; P("gp:   ThermalMargin rc=%d = %d s\n", tm(t, &v), v); }
            fn_gp_destroy_thermal dt = (fn_gp_destroy_thermal)S(h, "HMS_GamePerformance_DestroyThermalInfo");
            if (dt) dt(&t); }
    }
}

// ---- ABR ---------------------------------------------------------------------------------------------------------
typedef struct ABR_Context ABR_Context;
typedef ABR_Context *(*fn_abr_create)(int);
typedef int (*fn_abr_fps)(ABR_Context *, uint32_t);
typedef int (*fn_abr_range)(ABR_Context *, float, float);
typedef int (*fn_abr_act)(ABR_Context *);
typedef int (*fn_abr_next)(ABR_Context *, float *);
typedef int (*fn_abr_destroy)(ABR_Context **);
static void probe_abr(void) {
    void *h = L("libabr.so"); if (!h) return;
    fn_abr_create cr = (fn_abr_create)S(h, "HMS_ABR_CreateContext");
    if (!cr) { P("abr: create missing\n"); return; }
    ABR_Context *c = cr(0);   // RENDER_API_GLES
    P("abr: CreateContext(GLES)=%p\n", (void *)c);
    if (!c) return;
    fn_abr_fps f = (fn_abr_fps)S(h, "HMS_ABR_SetTargetFps");
    fn_abr_range r = (fn_abr_range)S(h, "HMS_ABR_SetScaleRange");
    fn_abr_act a = (fn_abr_act)S(h, "HMS_ABR_Activate");
    fn_abr_next nx = (fn_abr_next)S(h, "HMS_ABR_GetNextScale");
    fn_abr_destroy de = (fn_abr_destroy)S(h, "HMS_ABR_DestroyContext");
    if (f) P("abr: SetTargetFps(60)=%d\n", f(c, 60));
    if (r) P("abr: SetScaleRange(0.5,1.0)=%d\n", r(c, 0.5f, 1.0f));
    if (a) P("abr: Activate=%d\n", a(c));
    if (nx) { float s = -1; P("abr: GetNextScale rc=%d = %.3f\n", nx(c, &s), s); }
    if (de) P("abr: Destroy=%d\n", de(&c));
}

// ---- Frame Generation --------------------------------------------------------------------------------------------
typedef struct FG_Context_VK FG_Context_VK;
typedef struct {
    VkInstance vkInstance; VkPhysicalDevice vkPhysicalDevice; VkDevice vkDevice;
    uint8_t framesInFlight; PFN_vkGetInstanceProcAddr fnVulkanLoaderFunction;
} FG_CtxDesc;
typedef FG_Context_VK *(*fn_fg_create)(const FG_CtxDesc *);
typedef int (*fn_fg_destroy)(FG_Context_VK **);
typedef int (*fn_fg_alg)(FG_Context_VK *, const void *);
static void probe_fg(void) {
    void *h = L("libframegeneration.so"); if (!h) return;
    fn_fg_create cr = (fn_fg_create)S(h, "HMS_FG_CreateContext_VK");
    if (!cr) { P("fg: create missing\n"); return; }
    if (g_dev == VK_NULL_HANDLE) { P("fg: no Vulkan device, skipped\n"); return; }
    FG_CtxDesc d; memset(&d, 0, sizeof d);
    d.vkInstance = g_inst; d.vkPhysicalDevice = g_pd; d.vkDevice = g_dev;
    d.framesInFlight = 2; d.fnVulkanLoaderFunction = vkGetInstanceProcAddr;
    FG_Context_VK *c = cr(&d);
    P("fg: CreateContext_VK(framesInFlight 2)=%p\n", (void *)c);
    if (!c) return;
    fn_fg_alg am = (fn_fg_alg)S(h, "HMS_FG_SetAlgorithmMode_VK");
    if (am) { int mode[2] = {0, 0}; /* INTERPOLATION, ME_BASIC */ P("fg: SetAlgorithmMode(INTERP,BASIC)=%d\n", am(c, mode)); }
    fn_fg_destroy de = (fn_fg_destroy)S(h, "HMS_FG_DestroyContext_VK");
    if (de) P("fg: DestroyContext=%d\n", de(&c));
}

static void run_all(const char *bundle, int targetFps) {
    P("== stage-2 probe: Vulkan / XEngine / OpenGTX / GamePerformance / ABR / FrameGeneration ==\n");
    P("bundle=%s targetFps=%d\n", bundle, targetFps);
    if (vk_open()) { probe_xeg(); probe_fg(); }
    probe_gtx(bundle, targetFps);
    probe_gp(bundle);
    probe_abr();
    P("== end ==\n");
}

static napi_value js_run(napi_env env, napi_callback_info info) {
    size_t argc = 3; napi_value argv[3] = {0}; napi_get_cb_info(env, info, &argc, argv, NULL, NULL);
    char dir[1024] = {0}, bundle[256] = {0};
    int fps = 60;
    if (argc >= 1) { size_t n = 0; napi_get_value_string_utf8(env, argv[0], dir, sizeof dir, &n); }
    if (argc >= 2) { size_t n = 0; napi_get_value_string_utf8(env, argv[1], bundle, sizeof bundle, &n); }
    if (argc >= 3) { int32_t v = 60; napi_get_value_int32(env, argv[2], &v); fps = v; }
    g_len = 0; run_all(bundle, fps);
    if (dir[0]) { char p[1100]; snprintf(p, sizeof p, "%s/probe2.txt", dir); FILE *f = fopen(p, "w"); if (f) { fwrite(g_out, 1, g_len, f); fclose(f); } }
    napi_value s = NULL; napi_create_string_utf8(env, g_out, g_len, &s); return s;
}
EXTERN_C_START
static napi_value init(napi_env env, napi_value exports) {
    napi_value fn = NULL;
    napi_create_function(env, "run2", NAPI_AUTO_LENGTH, js_run, NULL, &fn);
    napi_set_named_property(env, exports, "run2", fn);
    return exports;
}
EXTERN_C_END
NAPI_MODULE(ohosprobe2, init)
