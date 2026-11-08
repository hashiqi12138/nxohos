/* probe_main.c — M0 探针主模块（libprobe.so）
 *
 * 结构：
 *   NAPI_MODULE_INIT → 起后台 worker 线程 → 逐项 fork 隔离执行检查 →
 *   父进程 waitpid 收尸归因 → 写 /data/local/tmp/probe_report.json（失败则回退
 *   应用沙盒 /data/storage/el2/base/files/）→ hilog 输出结论。
 *
 * 崩溃隔离（B 文档 M0 开头约定）：
 *   - ProbeReport 放 MAP_SHARED|MAP_ANONYMOUS 共享页，子进程增量写自己的字段；
 *   - 父进程 waitpid：WIFSIGNALED → 该项 crashed=true + sig 号，ok=false；
 *     WIFEXITED(非0) → ok=false + note "exit N"；正常 → 子进程自己写的结论生效；
 *   - 父进程另装 SIGSEGV/SIGILL/SIGBUS handler（write-only，async-signal-safe），
 *     防父进程自身故障导致报告缺失。
 *
 * 日志契约：启动后 hilog 必出现 "probe: started"（TC0-1-2 判定串，勿改）。
 */
#include "probe_checks.h"
#include "probe_report.h"

#include <hilog/log.h>

#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#include <deviceinfo.h>   /* OH_GetProductModel / OH_GetOSFullName（替代 __system_property_get：
                           * OHOS NDK 不提供 <sys/system_properties.h>，真机构建实测） */

#define PROBE_LOG_DOMAIN 0xE0F0
#define PROBE_LOG_TAG    "probe"

#define LOGI(...) OH_LOG_Print(LOG_APP, LOG_INFO, PROBE_LOG_DOMAIN, PROBE_LOG_TAG, __VA_ARGS__)
#define LOGE(...) OH_LOG_Print(LOG_APP, LOG_ERROR, PROBE_LOG_DOMAIN, PROBE_LOG_TAG, __VA_ARGS__)

/* 报告落盘路径：优先 /data/local/tmp（shell 可拉取），失败回退应用沙盒 files */
static const char *REPORT_PATH_PRIMARY = "/data/local/tmp/probe_report.json";
static const char *REPORT_PATH_FALLBACK = "/data/storage/el2/base/files/probe_report.json";

/* 共享报告（fork 前建立） */
static ProbeReport *g_r = NULL;

/* ---------- UTC ISO8601 ---------- */
static void iso8601_utc(char *buf, size_t cap)
{
    struct timespec ts;
    struct tm       tm;
    clock_gettime(CLOCK_REALTIME, &ts);
    gmtime_r(&ts.tv_sec, &tm);
    strftime(buf, cap, "%Y-%m-%dT%H:%M:%S", &tm);
    snprintf(buf + strlen(buf), cap - strlen(buf), ".%03ldZ", ts.tv_nsec / 1000000L);
}

/* ---------- 元信息 ---------- */
static void copy_str(char *dst, size_t cap, const char *src)
{
    snprintf(dst, cap, "%s", (src && src[0]) ? src : "unknown");
}

static void fill_meta(void)
{
    iso8601_utc(g_r->started_at, sizeof g_r->started_at);
    copy_str(g_r->device_model, sizeof g_r->device_model, OH_GetProductModel());
    copy_str(g_r->os_version, sizeof g_r->os_version, OH_GetOSFullName());
#ifdef DM_PROBE_BUILD
    copy_str(g_r->build, sizeof g_r->build, DM_PROBE_BUILD);
#else
    copy_str(g_r->build, sizeof g_r->build, "unknown");
#endif
}

/* ---------- 父进程自身的崩溃兜底（async-signal-safe：只 write） ---------- */
static void parent_fault_handler(int sig)
{
    static const char msg[] = "probe: parent fault sig=";
    char        buf[64];
    size_t      n   = 0;
    const char *dig = "0123456789";
    memcpy(buf, msg, sizeof msg - 1);
    n = sizeof msg - 1;
    if (sig >= 10) {
        buf[n++] = dig[sig / 10];
        buf[n++] = dig[sig % 10];
    } else {
        buf[n++] = dig[sig];
    }
    buf[n++] = '\n';
    ssize_t rc = write(2, buf, n);
    (void)rc;
    _exit(70);   /* 报告可能已写/未写：以 70 区别于正常退出 */
}

/* B 文档崩溃隔离约定：父进程安装 SIGSEGV/SIGILL/SIGBUS handler（在 fork 前调用） */
static void install_parent_handlers(void)
{
    struct sigaction sa;
    memset(&sa, 0, sizeof sa);
    sa.sa_handler = parent_fault_handler;
    sigemptyset(&sa.sa_mask);
    sa.sa_flags = SA_ONSTACK;   /* handler 内只 write+_exit，不作恢复 */
    sigaction(SIGSEGV, &sa, NULL);
    sigaction(SIGILL, &sa, NULL);
    sigaction(SIGBUS, &sa, NULL);
}

/* ---------- 进程内执行一项检查（不 fork） ----------
 * Vulkan 的 surface/swapchain 与进程级的图形连接强绑定，fork 后子进程
 * 里 vkCreateSwapchainKHR 会返回 VK_ERROR_SURFACE_LOST_KHR（真机实测
 * rc=-1000000000）。所以 vk_surface 这一项必须在父进程里跑，代价是万一
 * 图形栈崩了会带走整个进程 —— 崩溃前已写入的结论仍会落盘。 */
static bool run_inline(const char *name, ProbeItem *item, bool (*fn)(ProbeReport *))
{
    bool ok = fn(g_r);
    if (!ok && !item->note[0])
        snprintf(item->note, sizeof item->note, "inline fail");
    LOGI("probe: check %{public}s inline ok=%{public}d", name, ok ? 1 : 0);
    return ok;
}


/* ---------- 隔离执行一项检查 ----------
 * item 指向共享报告内对应 ProbeItem；fn 只写自己的结论。
 * 收尸带超时（WNOHANG 轮询）：多线程进程 fork 后子进程若撞上他线程持有的
 * malloc/stdio/加载器锁会死锁 —— 超时 SIGKILL 并归因，保证报告必然落盘。
 * 返回 true = 子进程正常退出（结论以 fn 写入为准）。 */
static bool run_isolated(const char *name, ProbeItem *item, bool (*fn)(ProbeReport *),
                         int timeout_s)
{
    pid_t pid = fork();
    if (pid < 0) {                       /* fork 失败：无法隔离，直接记失败 */
        snprintf(item->note, sizeof item->note, "fork failed");
        item->ok = false;
        return false;
    }
    if (pid == 0) {                      /* 子进程：跑完即 _exit（跳过 atexit/stdio） */
        /* 父进程装的 fault handler 是进程级、fork 会继承——子进程必须恢复默认
         * 处置，否则 SEGV/ILL/BUS 走 handler 变 _exit(70)，父进程只能看到
         * WIFEXITED(70)，crashed/sig 归因契约失效 */
        signal(SIGSEGV, SIG_DFL);
        signal(SIGILL, SIG_DFL);
        signal(SIGBUS, SIG_DFL);
        bool ok = fn(g_r);
        _exit(ok ? 0 : 1);
    }
    /* 父进程：轮询收尸 */
    int   status = 0;
    long  waited_ms = 0;
    pid_t wr;
    for (;;) {
        wr = waitpid(pid, &status, WNOHANG);
        if (wr == pid)
            break;
        if (wr < 0) {
            if (errno == EINTR)
                continue;                /* 仅 EINTR 重试 */
            snprintf(item->note, sizeof item->note, "waitpid errno=%d", errno);
            item->ok = false;
            return false;
        }
        if (waited_ms >= (long)timeout_s * 1000) {
            kill(pid, SIGKILL);
            while (waitpid(pid, &status, 0) < 0 && errno == EINTR) {
            }
            item->ok      = false;
            item->crashed = true;
            item->sig     = 0;
            snprintf(item->note, sizeof item->note, "timeout %ds", timeout_s);
            LOGE("probe: check %{public}s timeout %ds", name, timeout_s);
            return false;
        }
        struct timespec ms10 = { .tv_sec = 0, .tv_nsec = 10 * 1000 * 1000 };
        nanosleep(&ms10, NULL);
        waited_ms += 10;
    }
    if (WIFSIGNALED(status)) {
        item->ok      = false;
        item->crashed = true;
        item->sig     = WTERMSIG(status);
        snprintf(item->note, sizeof item->note, "trap sig=%d", WTERMSIG(status));
        LOGE("probe: check %{public}s crashed sig=%d", name, WTERMSIG(status));
        return false;
    }
    if (WIFEXITED(status) && WEXITSTATUS(status) != 0) {
        if (!item->ok && !item->crashed && !item->note[0])
            snprintf(item->note, sizeof item->note, "exit %d", WEXITSTATUS(status));
        return false;
    }
    return true;
}

/* ---------- 云调试适配：hilog 分块输出完整 JSON（无条件启用） ----------
 * 契约：每行 "probe: J|<seq>|<total>|<chunk>"，按 seq 拼接即完整 JSON；
 * 末行 "probe: J|END|<total>"。块 1600B（hilog 单条 4096B 上限留余量）。
 * PC 端 reassemble_hilog_json.py 消费（AGC 云调试无 hdc 的取回路径）。 */
#define HILOG_JSON_CHUNK 1600

static void hilog_dump_json(const char *json, size_t len)
{
    size_t total = (len + HILOG_JSON_CHUNK - 1) / HILOG_JSON_CHUNK;
    size_t seq   = 0;
    for (size_t off = 0; off < len; off += HILOG_JSON_CHUNK, seq++) {
        size_t n = (len - off < HILOG_JSON_CHUNK) ? (len - off) : HILOG_JSON_CHUNK;
        /* 注意：%.*s 必须写 %{public}.*s —— 否则 hilog 默认按 private 脱敏，
         * 真机日志里整块变成 <private>（2026-11-06 云调试实测） */
        LOGI("probe: J|%{public}zu|%{public}zu|%{public}.*s", seq, total, (int)n,
             json + off);
    }
    LOGI("probe: J|END|%{public}zu (len=%{public}zu)", total, len);
}

/* ---------- worker：跑全部检查并落盘 ---------- */
static void *probe_worker(void *arg)
{
    (void)arg;

    LOGI("probe: started");              /* TC0-1-2 契约串 */

    fill_meta();

    /* AOT 形态的 dlopen/dlsym 必须在 fork 之前做完：子进程里调链接器会与
     * fork 瞬间持有链接器锁的线程互等（实测超时 60s）。 */
    probe_aot_preload();

    /* 执行顺序契约：
     *   - 无窗口依赖的 mem/regs 检查先行（vk 失败不影响它们有结论）；
     *   - vk 依赖 XComponent 窗口，而 OnSurfaceCreated 在 Ability 首帧后才触发
     *     —— 父进程先 probe_vk_wait_window(10) 等注入，再 fork 子进程，
     *     消除"子进程等窗口、窗口在父进程侧稍后才注入"的时序竞态；
     *   - vk 全链路 300 帧 FIFO@60Hz + dump，超时 240s；其余 60s。 */
    run_isolated("mem_reserve_5g", &g_r->mem_reserve_5g, probe_mem_reserve5g, 60);
    run_isolated("mem_headroom_8g", &g_r->mem_headroom_8g, probe_mem_headroom, 60);
    run_isolated("mem_commit_2g", &g_r->mem_commit_2g, probe_mem_commit, 60);
    run_isolated("tpidr_rw", &g_r->tpidr_rw, probe_tpidr_rw, 60);
    run_isolated("tpidrro", &g_r->tpidrro, probe_tpidrro, 60);
    run_isolated("cntpct", &g_r->cntpct, probe_cntpct, 60);
    run_isolated("tls_per_thread", &g_r->tls_per_thread, probe_tls_per_thread, 60);
    run_isolated("futex", &g_r->futex, probe_futex, 60);
    run_isolated("wx", &g_r->wx, probe_wx, 60);
    run_isolated("mini_nso", &g_r->mini_nso, probe_mini_nso, 60);
    run_isolated("aot_nso", &g_r->aot_nso, probe_aot_nso, 60);
    probe_vk_wait_window(30);   /* ArkTS onLoad 传 surfaceId 需要等首帧，30s 兜底 */
    run_inline("vk_surface", &g_r->vk_surface, probe_vk_run);

    iso8601_utc(g_r->finished_at, sizeof g_r->finished_at);

    /* 组装一次，落盘 + （可选）hilog 分块共用 */
    size_t json_len = 0;
    char  *json     = probe_report_json(g_r, &json_len);
    if (!json) {
        LOGE("probe: report json build failed (OOM?)");
        return NULL;
    }

    /* 落盘：主路径失败则回退沙盒路径（TC0-1-3 的 hdc 写沙盒结论影响此处可达性） */
    const char *used = REPORT_PATH_PRIMARY;
    if (probe_report_write_str(REPORT_PATH_PRIMARY, json, json_len) != 0) {
        used = REPORT_PATH_FALLBACK;
        if (probe_report_write_str(REPORT_PATH_FALLBACK, json, json_len) != 0)
            LOGE("probe: report write failed on both paths");
    }
    LOGI("probe: report at %{public}s", used);

    /* 云调试/无 hdc 环境：报告 JSON 经 hilog 分块输出（PC 端
     * reassemble_hilog_json.py 从导出日志重组）。无条件输出——云真机无法
     * 注入环境变量，且几 KB 日志对 hdc 真机路径无碍。 */
    hilog_dump_json(json, json_len);
    free(json);

    /* 失败项汇总进 hilog（G0.x 闸门留档素材） */
    const struct {
        const char *     name;
        const ProbeItem *it;
    } items[] = {
        {"vk_surface", &g_r->vk_surface},           {"mem_reserve_5g", &g_r->mem_reserve_5g},
        {"mem_headroom_8g", &g_r->mem_headroom_8g}, {"mem_commit_2g", &g_r->mem_commit_2g},
        {"tpidr_rw", &g_r->tpidr_rw},               {"tpidrro", &g_r->tpidrro},
        {"cntpct", &g_r->cntpct},                   {"tls_per_thread", &g_r->tls_per_thread},
        {"futex", &g_r->futex},                     {"wx", &g_r->wx},
        {"mini_nso", &g_r->mini_nso},               {"aot_nso", &g_r->aot_nso},
    };
    for (size_t i = 0; i < sizeof items / sizeof items[0]; i++) {
        if (items[i].it->ok)
            continue;
        LOGE("probe: FAIL %{public}s crashed=%{public}d sig=%{public}d note=%{public}s",
             items[i].name, items[i].it->crashed, items[i].it->sig, items[i].it->note);
    }
    LOGI("probe: bcn_all=%{public}d astc4x4=%{public}d astc8x8=%{public}d "
         "contiguous=%{public}d cntfrq=%{public}llu",
         (int)g_r->bcn_all, (int)g_r->astc_4x4, (int)g_r->astc_8x8,
         (int)g_r->headroom_contiguous, (unsigned long long)g_r->cntfrq_hz);

    return NULL;
}

/* ---------- NAPI：模块加载即自启 worker ---------- */
/* 窗口获取走 surfaceId：本 SDK（HarmonyOS 6 / API 20）的 XComponent 头只剩
 * OH_NativeXComponent_GetNativeXComponent(ArkUI_NodeHandle)，旧的
 * napi_value 版接口已移除。改用官方 API 12+ 路径：
 *   ArkTS: xComponentController.getXComponentSurfaceId() → native.setSurfaceId(id)
 *   native: OH_NativeWindow_CreateNativeWindowFromSurfaceId(id, &window)
 * 依赖更少、行为更确定，也顺带避开了 XComponent 回调注册的 API 变更。 */
#include <napi/native_api.h>
#include <native_window/external_window.h>
#include <pthread.h>

static void *worker_trampoline(void *arg)
{
    return probe_worker(arg);
}

/* native.setSurfaceId(surfaceId: string) —— ArkTS 侧 onLoad 时调用一次 */
static napi_value js_set_surface_id(napi_env env, napi_callback_info info)
{
    napi_value undef = NULL;
    napi_value argv[1] = { NULL };
    size_t     argc    = 1;
    napi_get_undefined(env, &undef);
    napi_get_cb_info(env, info, &argc, argv, NULL, NULL);
    if (argc < 1) {
        LOGE("probe: setSurfaceId called without argument");
        return undef;
    }
    char   buf[32] = { 0 };
    size_t len     = 0;
    if (napi_get_value_string_utf8(env, argv[0], buf, sizeof buf, &len) != napi_ok) {
        LOGE("probe: setSurfaceId argument is not a string");
        return undef;
    }
    uint64_t       sid = strtoull(buf, NULL, 10);
    OHNativeWindow *win = NULL;
    int32_t        rc  = OH_NativeWindow_CreateNativeWindowFromSurfaceId(sid, &win);
    LOGI("probe: setSurfaceId=%{public}s rc=%{public}d win=%{public}p", buf, rc,
         (void *)win);
    if (rc == 0 && win)
        probe_vk_set_window(win);
    else
        LOGE("probe: create native window from surfaceId failed");
    return undef;
}

EXTERN_C_START
static napi_value probe_module_init(napi_env env, napi_value exports)
{
    (void)env;

    /* 父进程自身崩溃兜底：必须在任何 fork 之前安装 */
    install_parent_handlers();

    /* 导出 setSurfaceId（ArkTS 拿到 XComponent 的 surfaceId 后调用）。
     * 用 create_function + set_named_property：本 SDK 的
     * napi_property_descriptor.name 是 napi_value 而非 const char*。 */
    napi_value fn = NULL;
    if (napi_create_function(env, "setSurfaceId", NAPI_AUTO_LENGTH,
                             js_set_surface_id, NULL, &fn) == napi_ok && fn)
        napi_set_named_property(env, exports, "setSurfaceId", fn);
    else
        LOGE("probe: export setSurfaceId failed");
    LOGI("probe: napi module init");

    /* 共享报告页：fork 前建立（MAP_SHARED 使子进程写入对父进程可见） */
    void *page = mmap(NULL, sizeof(ProbeReport), PROT_READ | PROT_WRITE,
                      MAP_SHARED | MAP_ANONYMOUS, -1, 0);
    if (page == MAP_FAILED) {
        LOGE("probe: shared report mmap failed, abort");
        return exports;
    }
    g_r = (ProbeReport *)page;
    probe_report_init(g_r);

    pthread_t th;
    pthread_attr_t attr;
    pthread_attr_init(&attr);
    pthread_attr_setdetachstate(&attr, PTHREAD_CREATE_DETACHED);
    if (pthread_create(&th, &attr, worker_trampoline, NULL) != 0) {
        LOGE("probe: worker thread create failed");
        return exports;
    }
    pthread_attr_destroy(&attr);
    return exports;
}
EXTERN_C_END

/* 模块名与 probe/hap 的 XComponent libraryname / index.ets import 对齐 */
NAPI_MODULE(probe, probe_module_init)
