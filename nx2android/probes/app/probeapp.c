// probeapp: runs cpuprobe, vkprobe and vkzc inside an ordinary app process (SELinux domain untrusted_app), where an nxnative-style host would run.
// A NativeActivity with no Java code. ANativeActivity_onCreate starts two threads:
//   · the probe thread writes everything to <files>/results.txt and mirrors it to logcat (tag nx2a);
//   · the UI thread draws that file into the window with a 5x7 bitmap font (software rendering, ANativeWindow_lock) and scrolls it by dragging.
// The app stays open until the user leaves it (Back closes it). Results from the PC: adb shell run-as org.nx2a.probe cat files/results.txt
#include <android/input.h>
#include <android/log.h>
#include <android/looper.h>
#include <android/native_activity.h>
#include <android/native_window.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include "font5x7.h"

int cpuprobe_main(int argc, char **argv);
int vkprobe_main(void);
int vkzc_main(int argc, char **argv);

static char g_out[512];
static atomic_int g_done;

// ---------------- probes ----------------
static void cat_file(const char *path, const char *what) {
    FILE *f = fopen(path, "r"); char line[512];
    if (!f) { printf("%s: (unreadable)\n", what); return; }
    if (fgets(line, sizeof line, f)) printf("%s: %s%s", what, line, strchr(line, '\n') ? "" : "\n");
    fclose(f);
}
static void *run_probes(void *arg) {
    ANativeActivity *act = arg;
    setenv("PROBE_TMP", act->internalDataPath, 1);
    if (!freopen(g_out, "w", stdout)) { __android_log_print(ANDROID_LOG_ERROR, "nx2a", "cannot write %s", g_out); return NULL; }
    setvbuf(stdout, NULL, _IOLBF, 0);
    printf("== app context\n");
    cat_file("/proc/self/attr/current", "selinux");
    printf("uid %d  sdk %d  files %s\n", getuid(), act->sdkVersion, act->internalDataPath);
    cat_file("/proc/self/oom_score_adj", "oom_score_adj");
    char *a1[] = {"cpuprobe", NULL};
    printf("\n######## cpuprobe\n"); cpuprobe_main(1, a1);
    printf("\n######## vkprobe\n"); vkprobe_main();
    char *a3[] = {"vkzc", NULL};
    printf("\n######## vkzc\n"); vkzc_main(1, a3);
    printf("\nDONE\n"); fflush(stdout);
    FILE *f = fopen(g_out, "r"); char line[1024];
    while (f && fgets(line, sizeof line, f)) __android_log_write(ANDROID_LOG_INFO, "nx2a", line);
    if (f) fclose(f);
    atomic_store(&g_done, 1);
    return NULL;
}

// ---------------- UI ----------------
static pthread_mutex_t g_win_lock = PTHREAD_MUTEX_INITIALIZER;   // held while drawing, so onNativeWindowDestroyed can't return mid-frame
static ANativeWindow *g_win;
static ALooper *g_looper;
static AInputQueue *g_queue;
static pthread_mutex_t g_ready_lock = PTHREAD_MUTEX_INITIALIZER; static pthread_cond_t g_ready = PTHREAD_COND_INITIALIZER;
static atomic_int g_dirty = 1;
static int g_scroll, g_last_y, g_dragging;
enum { SCALE = 3, CW = 6 * SCALE, CH = 9 * SCALE, MARGIN = 24, TOP = 140, BAR = TOP + 2 * CH };   // TOP: room for the status bar

static uint32_t rgb(uint32_t r, uint32_t g, uint32_t b) { return 0xff000000u | b << 16 | g << 8 | r; }   // RGBX_8888 in memory: R G B X
static uint32_t line_color(const char *s) {
    if (strstr(s, "FAILED") || strstr(s, "mismatch") || strstr(s, "WARN") || !strncmp(s, "NO ", 3) || strstr(s, "unexpected")) return rgb(255, 140, 90);
    if (strstr(s, "SIGNAL")) return rgb(255, 210, 90);
    if (strstr(s, "WORKS") || strstr(s, "] ok") || !strncmp(s, "yes ", 4)) return rgb(130, 220, 140);
    if (!strncmp(s, "==", 2) || !strncmp(s, "####", 4)) return rgb(110, 190, 255);
    return rgb(225, 225, 225);
}
static void draw_text(ANativeWindow_Buffer *b, int x, int y, const char *s, int n, uint32_t c) {
    uint32_t *px = b->bits;
    for (int i = 0; i < n; i++, x += CW) {
        unsigned ch = (unsigned char)s[i]; const unsigned char *g = FONT5X7[(ch >= 32 && ch < 127 ? ch : '?') - 32];
        if (x + CW > b->width) break;
        for (int col = 0; col < 5; col++)
            for (int row = 0; row < 7; row++) {
                if (!(g[col] >> row & 1)) continue;
                int y0 = y + row * SCALE, x0 = x + col * SCALE;
                if (y0 < 0 || y0 + SCALE > b->height) continue;
                for (int dy = 0; dy < SCALE; dy++) for (int dx = 0; dx < SCALE; dx++) px[(y0 + dy) * b->stride + x0 + dx] = c;
            }
    }
}
static char *read_all(size_t *n) {
    FILE *f = fopen(g_out, "r"); if (!f) { *n = 0; return NULL; }
    char *buf = NULL; size_t cap = 0, len = 0, r; char tmp[4096];
    while ((r = fread(tmp, 1, sizeof tmp, f)) > 0) { if (len + r + 1 > cap) buf = realloc(buf, cap = (len + r + 1) * 2); memcpy(buf + len, tmp, r); len += r; }
    fclose(f); if (buf) buf[len] = 0; *n = len; return buf;
}
static void render(void) {
    pthread_mutex_lock(&g_win_lock);
    if (!g_win) { pthread_mutex_unlock(&g_win_lock); return; }
    ANativeWindow_Buffer b;
    if (ANativeWindow_lock(g_win, &b, NULL)) { pthread_mutex_unlock(&g_win_lock); return; }
    uint32_t *px = b.bits, bg = rgb(18, 20, 26), bar = rgb(36, 40, 52);
    for (int y = 0; y < b.height; y++) for (int x = 0; x < b.width; x++) px[y * b.stride + x] = y < BAR ? bar : bg;
    size_t n; char *text = read_all(&n);
    int cols = (b.width - 2 * MARGIN) / CW, view = b.height - BAR - MARGIN, total = 0;
    // wrapped lines: count first, so the scroll can be clamped, then draw what is visible
    for (int pass = 0; pass < 2; pass++) {
        int y = BAR + MARGIN / 2 - g_scroll, k = 0;
        for (char *s = text; s && *s;) {
            char *e = strchr(s, '\n'); int len = e ? (int)(e - s) : (int)strlen(s);
            char one[1024]; snprintf(one, sizeof one, "%.*s", len, s);   // colour by this line only, not the rest of the file
            uint32_t c = line_color(one); int indent = 0;
            do {
                int take = len < cols - indent ? len : cols - indent;
                if (pass && y + CH > BAR && y < b.height) draw_text(&b, MARGIN + indent * CW, y, s, take, c);
                s += take; len -= take; y += CH; k++; indent = 2;   // continuation lines are indented
            } while (len > 0);
            s = e ? e + 1 : s + strlen(s);
        }
        total = k * CH;
        int max = total > view ? total - view : 0;
        if (g_scroll > max) g_scroll = max;
        if (g_scroll < 0) g_scroll = 0;
    }
    char head[256];
    snprintf(head, sizeof head, "NX2A Probe  -  %s  -  drag to scroll, Back to close", atomic_load(&g_done) ? "all tests done" : "running tests...");
    draw_text(&b, MARGIN, TOP + CH / 2, head, strlen(head), atomic_load(&g_done) ? rgb(130, 220, 140) : rgb(255, 210, 90));
    if (total > view) {   // scroll bar
        int h = view * view / total, top = BAR + (view - h) * g_scroll / (total - view);
        for (int y = top; y < top + h && y < b.height; y++) for (int x = b.width - 10; x < b.width - 4; x++) px[y * b.stride + x] = rgb(90, 96, 120);
    }
    free(text);
    ANativeWindow_unlockAndPost(g_win);
    pthread_mutex_unlock(&g_win_lock);
}
static void handle_input(void) {
    AInputEvent *ev;
    while (g_queue && AInputQueue_getEvent(g_queue, &ev) >= 0) {
        if (AInputQueue_preDispatchEvent(g_queue, ev)) continue;
        int handled = 0;
        if (AInputEvent_getType(ev) == AINPUT_EVENT_TYPE_MOTION) {
            int act = AMotionEvent_getAction(ev) & AMOTION_EVENT_ACTION_MASK, y = (int)AMotionEvent_getY(ev, 0);
            if (act == AMOTION_EVENT_ACTION_DOWN) { g_dragging = 1; g_last_y = y; }
            else if (act == AMOTION_EVENT_ACTION_MOVE && g_dragging) { g_scroll += g_last_y - y; g_last_y = y; atomic_store(&g_dirty, 1); }
            else if (act == AMOTION_EVENT_ACTION_UP || act == AMOTION_EVENT_ACTION_CANCEL) g_dragging = 0;
            handled = 1;
        }
        AInputQueue_finishEvent(g_queue, ev, handled);   // keys stay unhandled, so Back closes the activity
    }
}
static void *ui_thread(void *arg) {
    (void)arg;
    pthread_mutex_lock(&g_ready_lock);
    g_looper = ALooper_prepare(0);
    pthread_cond_signal(&g_ready); pthread_mutex_unlock(&g_ready_lock);
    size_t last_size = (size_t)-1; int last_done = -1;
    for (;;) {
        ALooper_pollOnce(150, NULL, NULL, NULL);   // wakes for input (ident 1) or every 150 ms to pick up new probe output
        handle_input();
        FILE *f = fopen(g_out, "r"); size_t sz = 0; if (f) { fseek(f, 0, SEEK_END); sz = ftell(f); fclose(f); }
        if (sz != last_size || atomic_load(&g_done) != last_done) { last_size = sz; last_done = atomic_load(&g_done); atomic_store(&g_dirty, 1); }
        if (atomic_exchange(&g_dirty, 0)) render();
    }
    return NULL;
}

// ---------------- activity callbacks (main thread) ----------------
static void on_window_created(ANativeActivity *a, ANativeWindow *w) {
    (void)a; ANativeWindow_setBuffersGeometry(w, 0, 0, AHARDWAREBUFFER_FORMAT_R8G8B8X8_UNORM);
    pthread_mutex_lock(&g_win_lock); g_win = w; pthread_mutex_unlock(&g_win_lock); atomic_store(&g_dirty, 1); ALooper_wake(g_looper);
}
static void on_window_destroyed(ANativeActivity *a, ANativeWindow *w) { (void)a; (void)w; pthread_mutex_lock(&g_win_lock); g_win = NULL; pthread_mutex_unlock(&g_win_lock); }
static void on_window_redraw(ANativeActivity *a, ANativeWindow *w) { (void)a; (void)w; atomic_store(&g_dirty, 1); ALooper_wake(g_looper); }
static void on_queue_created(ANativeActivity *a, AInputQueue *q) { (void)a; g_queue = q; AInputQueue_attachLooper(q, g_looper, 1, NULL, NULL); }
static void on_queue_destroyed(ANativeActivity *a, AInputQueue *q) { (void)a; AInputQueue_detachLooper(q); g_queue = NULL; }
static void on_destroy(ANativeActivity *a) { (void)a; _exit(0); }   // one run per process: the next launch starts fresh

void ANativeActivity_onCreate(ANativeActivity *act, void *saved, size_t saved_size) {
    (void)saved; (void)saved_size;
    snprintf(g_out, sizeof g_out, "%s/results.txt", act->internalDataPath);
    act->callbacks->onDestroy = on_destroy;
    act->callbacks->onNativeWindowCreated = on_window_created;
    act->callbacks->onNativeWindowDestroyed = on_window_destroyed;
    act->callbacks->onNativeWindowRedrawNeeded = on_window_redraw;
    act->callbacks->onInputQueueCreated = on_queue_created;
    act->callbacks->onInputQueueDestroyed = on_queue_destroyed;
    pthread_t t;
    pthread_mutex_lock(&g_ready_lock);
    pthread_create(&t, NULL, ui_thread, NULL); pthread_detach(t);
    while (!g_looper) pthread_cond_wait(&g_ready, &g_ready_lock);   // the input queue is attached to the UI thread's looper
    pthread_mutex_unlock(&g_ready_lock);
    pthread_create(&t, NULL, run_probes, act); pthread_detach(t);
}
