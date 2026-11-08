// HarmonyOS front end (the counterpart of dm/android/front_android.c and dm/ios/front_ios.m), implementing the host's front.h.
// It is much smaller than Android's because the window is not created here: the ArkTS page owns an XComponent and hands its surface
// id to napi_bridge.c, which calls dm_ohos_front_set_surface(). What is left is the win32-sized part the host actually needs:
//   - the window handle and its size (the Vulkan backend's dm_front_window()),
//   - front_run / front_metal_layer, since the guest runs on a thread this module starts, never on the ArkTS main thread,
//   - the paths and environment variables the host reads at startup (DM_ROOT / DM_ROMFS / DM_SAVE / DM_TEMP / DM_VK_SHADERS).
#include "dm.h"
#include "front.h"
#include <native_window/external_window.h>
#include <pthread.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

static OHNativeWindow *g_win; static int g_win_w, g_win_h; static pthread_mutex_t g_win_mu = PTHREAD_MUTEX_INITIALIZER;

OHNativeWindow *dm_front_window(void) { pthread_mutex_lock(&g_win_mu); OHNativeWindow *w = g_win; pthread_mutex_unlock(&g_win_mu); return w; }
void dm_ohos_surface_size(int *w, int *h) {
    pthread_mutex_lock(&g_win_mu);
    int a = g_win_w, b = g_win_h;
    if ((!a || !b) && g_win) OH_NativeWindow_NativeWindowHandleOpt(g_win, GET_BUFFER_GEOMETRY, &a, &b);
    pthread_mutex_unlock(&g_win_mu);
    if (w) *w = a; if (h) *h = b;
}
// gfx_vk_present_ohos.c: dropped from a --gfx null build (build_ohos.py), which still links this file
void dm_ohos_window_resized(void) __attribute__((weak));

// Called by napi_bridge.c when the XComponent's ArkTS side reports (and repaints) its surface. w/h are the surface's pixels.
void dm_ohos_front_set_surface(uint64_t surface_id, int w, int h) {
    OHNativeWindow *win = NULL;
    int rc = OH_NativeWindow_CreateNativeWindowFromSurfaceId(surface_id, &win);
    if (rc || !win) { LOG("[ERR] front: surface %llu: no native window (rc=%d)", (unsigned long long)surface_id, rc); return; }
    pthread_mutex_lock(&g_win_mu); OHNativeWindow *old = g_win; g_win = win; g_win_w = w; g_win_h = h; pthread_mutex_unlock(&g_win_mu);
    LOG("front: surface %llu is up (%dx%d)", (unsigned long long)surface_id, w, h);
    if (dm_ohos_window_resized) dm_ohos_window_resized();   // drops the swapchain built on the old surface before that window is destroyed
    if (old && old != win) OH_NativeWindow_DestroyNativeWindow(old);
}
void dm_ohos_front_surface_lost(void) {
    pthread_mutex_lock(&g_win_mu); OHNativeWindow *old = g_win; g_win = NULL; pthread_mutex_unlock(&g_win_mu);
    if (dm_ohos_window_resized) dm_ohos_window_resized();
    if (old) OH_NativeWindow_DestroyNativeWindow(old);
    LOG("front: surface lost, waiting for the next one");
}

void front_run(void (*guest)(void)) { guest(); }   // the caller is already this module's own thread (napi_bridge.c)
void *front_metal_layer(void) { return dm_front_window(); }
int front_screen_max_fps(void) { return 0; }      // not known here; the present mode paces the game
void front_screen_match(int fps) { (void)fps; }
int front_text_input(const char *title, const char *sub, const char *guide, const char *initial, int max_chars, char *out, int out_cap) {
    (void)title; (void)sub; (void)guide; (void)initial; (void)max_chars; (void)out; (void)out_cap;
    return -1;   // no native input box yet: the caller (hle_swkbd.c) treats -1 as "cancelled"
}
void front_save_failed(int err) { LOG("[ERR] front: a save was not written (errno %d); the previous commit is unchanged", err); }

// ---- startup: paths, environment, the game thread ---------------------------------------------------------------------------------
static void mkdirs(const char *p) { char b[1024]; snprintf(b, sizeof b, "%s", p); for (char *s = b + 1; *s; s++) if (*s == '/') { *s = 0; mkdir(b, 0700); *s = '/'; } mkdir(b, 0700); }
static char g_buf[4][1024];
static void env_file(const char *path) {   // KEY=VALUE per line (# comments), as front_android.c: the pack's switches without a rebuild
    FILE *f = fopen(path, "r"); char line[1024];
    if (!f) { LOG("front: no %s", path); return; }
    while (fgets(line, sizeof line, f)) {
        char *e = line + strlen(line); while (e > line && (e[-1] == '\n' || e[-1] == '\r' || e[-1] == ' ')) *--e = 0;
        if (!line[0] || line[0] == '#') continue;
        char *eq = strchr(line, '='); if (!eq) continue; *eq = 0; setenv(line, eq + 1, 1);
    }
    fclose(f);
}
// A pack switch that names a file or folder in the pack is written relative to the pack's root, which only the app knows: Android's pack sits under
// /sdcard/Android/data/<package>/files and its env.txt is absolute, HarmonyOS's pack is staged into the app's sandbox by the ArkTS page, so its
// env.txt holds bare paths ('aoc', 'nintendo_ext.ttf') that are put under this root here. Every pack switch the host reads as a path belongs in this
// list (today DM_AOC_DIR, DM_SHARED_FONT_EXT and DM_FPS_LOCKS are all of them).
static void env_under_root(const char *key, const char *root, char *buf, size_t cap) {
    const char *v = getenv(key);
    if (!v || !v[0] || v[0] == '/') return;
    int n = snprintf(buf, cap, "%s/%s", root, v);
    if (n < 0 || (size_t)n >= cap) { LOG("[ERR] front: %s=%s does not fit in %zu bytes: left as it is", key, v, cap); return; }
    setenv(key, buf, 1);
    LOG("front: %s=%s", key, buf);
}

// Start the host: argv[1] is the directory the modules were placed in (load_modules reads the NSOs from there); the module names come
// from the game profile. Called on the thread napi_bridge.c created; returns when the game exits.
void dm_ohos_game_start(const char *root, const char *save, const char *temp) {
    mkdirs(root);
    setenv("DM_ROOT", root, 1);
    snprintf(g_buf[0], sizeof g_buf[0], "%s/RomFS", root);  setenv("DM_ROMFS", g_buf[0], 1);
    mkdirs(save); setenv("DM_SAVE", save, 1);
    mkdirs(temp); setenv("DM_TEMP", temp, 1);
    snprintf(g_buf[1], sizeof g_buf[1], "%s/shaders.dmsv", root); setenv("DM_VK_SHADERS", g_buf[1], 1);
    snprintf(g_buf[2], sizeof g_buf[2], "%s/env.txt", root); env_file(g_buf[2]);
    // Crash reports and guest-thread dumps use stderr directly (unlike LOG, which goes to hilog): keep them beside the pack as Android does.
    snprintf(g_buf[2], sizeof g_buf[2], "%s/log.txt", root);
    if (freopen(g_buf[2], "w", stderr)) setvbuf(stderr, NULL, _IOLBF, 0);
    env_under_root("DM_AOC_DIR", root, g_buf[2], sizeof g_buf[2]);
    env_under_root("DM_SHARED_FONT_EXT", root, g_buf[2], sizeof g_buf[2]);
    env_under_root("DM_FPS_LOCKS", root, g_buf[2], sizeof g_buf[2]);
    if (!getenv("DM_AOC_DIR")) {   // a pack with DLC that names no folder: the pack's own aoc/
        struct stat st; snprintf(g_buf[2], sizeof g_buf[2], "%s/aoc", root);
        if (!stat(g_buf[2], &st) && S_ISDIR(st.st_mode)) setenv("DM_AOC_DIR", g_buf[2], 1);
    }
    unsetenv("DM_CTL");
    LOG("front: root %s", root);
    for (int i = 0; i < 600 && !dm_front_window(); i++) usleep(20 * 1000);   // the XComponent surface normally arrives in a few frames
    if (!dm_front_window()) LOG("[ERR] front: no window after 12 s: the game starts headless (nothing will be shown)");
    // the same layout as the Android pack: mods/ holds the rewritten modules (load_modules reads <dir>/<name>.patched.bin)
    snprintf(g_buf[3], sizeof g_buf[3], "%s/mods", root);
    int argc = 2; static char *argv[8]; argv[0] = (char *)"dmhost"; argv[1] = g_buf[3];
    int dm_main(int, char **);   // host/main.c
    dm_main(argc, argv);
    LOG("front: the game returned");
}
