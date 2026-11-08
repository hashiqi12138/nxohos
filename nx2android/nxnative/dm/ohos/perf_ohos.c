// HarmonyOS performance policy (the counterpart of dm/android/perf_android.c): the adaptive frame rate and the replay thread's CPU placement.
// Android's Android Dynamic Performance Framework hint is left out (HarmonyOS has no such API here); the pacing logic, which is what decides
// whether the game holds 60 or steps to 40/30, is unchanged: it is judged on the frames' work and on the frames that missed the display.
#include "dm.h"
#include <math.h>
#include <sched.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>
#include "gfx_vk.h"

static int64_t perf_now(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return (int64_t)t.tv_sec * 1000000000ll + t.tv_nsec; }
static int gettid_(void) { return (int)syscall(SYS_gettid); }

// DM_PIN_REPLAY: the replay thread kept to the fastest cores (prime: the highest maximum clock; big: every core above the lowest).
void dm_perf_replay_thread(void) {
    const char *e = getenv("DM_PIN_REPLAY"); if (!e || !*e || !strcmp(e, "0")) return;
    int prime = !strcmp(e, "prime"); long n = sysconf(_SC_NPROCESSORS_CONF); if (n > 32) n = 32;
    long mhz[32] = {0}, hi = 0, lo = 0;
    for (long c = 0; c < n; c++) { char p[96]; snprintf(p, sizeof p, "/sys/devices/system/cpu/cpu%ld/cpufreq/cpuinfo_max_freq", c); FILE *f = fopen(p, "r");
        if (f) { if (fscanf(f, "%ld", &mhz[c]) != 1) mhz[c] = 0; fclose(f); } mhz[c] /= 1000; if (mhz[c] > hi) hi = mhz[c]; if (mhz[c] && (!lo || mhz[c] < lo)) lo = mhz[c]; }
    if (!hi || hi == lo) { LOG("[perf] DM_PIN_REPLAY: the cores' clocks are unknown or all alike, not pinned"); return; }
    cpu_set_t set; CPU_ZERO(&set); char l[96]; size_t o = 0;
    for (long c = 0; c < n; c++) if (prime ? mhz[c] == hi : mhz[c] > lo) { CPU_SET(c, &set); o += snprintf(l + o, sizeof l - o, " %ld", c); }
    if (sched_setaffinity(0, sizeof set, &set)) { LOG("[perf] DM_PIN_REPLAY: sched_setaffinity refused"); return; }
    LOG("[perf] replay thread on CPUs%s (%s, up to %ld MHz)", l, prime ? "prime" : "big", hi);
}

// ---- adaptive frame rate: 60 → 40 → 30 fps when the frames do not fit the period the game asks for, and back when they fit with room.
static const int64_t STEPS[] = {16666667, 25000000, 33333333};   // 60, 40, 30 fps
static int g_step, g_adapt = -1; static int64_t g_work_hist[64]; static int g_nwork;
static int64_t g_last_change, g_last_up, g_up_hold = 2000000000; static int g_ok_frames; static uint64_t g_game_period;
static int64_t work_pct(int q) {
    int n = g_nwork < 64 ? g_nwork : 64; if (n < 16) return 0;
    int64_t v[64]; memcpy(v, g_work_hist, sizeof(int64_t) * (size_t)n);
    for (int i = 1; i < n; i++) { int64_t x = v[i]; int j = i - 1; while (j >= 0 && v[j] > x) { v[j + 1] = v[j]; j--; } v[j + 1] = x; }
    return v[(n * q) / 100];
}
static int64_t work_p95(void) { return work_pct(95); }
uint64_t dm_present_period_adapt(uint64_t period) {
    if (g_adapt < 0) { const char *e = getenv("DM_ADAPTIVE_FPS"); g_adapt = !(e && !atoi(e)); }
    if (!g_adapt || !period) return period;
    g_game_period = period;
    int64_t want = STEPS[g_step];
    return (uint64_t)want > period ? (uint64_t)want : period;   // never faster than the game asks
}
static int fps_pin(void) {
    static unsigned k; static int pin = -1; if (k++ % 30) return pin;
    const char *e = getenv("DM_FPS_PIN"); int v = e ? atoi(e) : 0, st = v == 60 ? 0 : v == 40 ? 1 : v == 30 ? 2 : -1;
    if (st != pin) { pin = st; LOG("[perf] adaptive frame rate %s", st < 0 ? "free again" : "pinned"); }
    return pin;
}
static int64_t g_frame_cost, g_frame_gpu;
void dm_perf_frame_cost(int64_t replay_ns, int64_t gpu_ns) { g_frame_cost = replay_ns > gpu_ns ? replay_ns : gpu_ns; g_frame_gpu = gpu_ns; }
static uint8_t g_late[64]; static int g_nshown; static unsigned g_held_back;
void dm_perf_present(double iv_ms) {   // the frames' time on screen (hle_nvn.c): longer than 1.5 periods of the step, or discarded, is late
    if (iv_ms == 0) return;
    double per = STEPS[g_step] / 1e6, lim = per * 1.5;
    g_late[g_nshown++ % 64] = iv_ms < 0 || iv_ms > lim;
}
static int shown_late(void) {
    int n = g_nshown < 64 ? g_nshown : 64, m = 0; if (n < 32) return -1;
    for (int i = 0; i < n; i++) m += g_late[i];
    return m * 64 / n;
}
static void adapt_frame(int64_t work, int64_t now) {
    if (g_frame_cost > 0 && g_frame_cost < 1500000 && work > STEPS[g_step]) return;   // a load, not a frame the rate could fit
    if (g_frame_cost > work) work = g_frame_cost;
    if (!g_adapt) return;
    g_work_hist[g_nwork++ % 64] = work;
    { static unsigned k; if (++k % 600 == 0) LOG("[perf] adaptive frame rate: %.0f fps, frame work p50 %.1f / p90 %.1f / p95 %.1f ms (last %.1f ms); late on screen %d of 64, step-downs held back %u", 1e9 / STEPS[g_step], work_pct(50) / 1e6, work_pct(90) / 1e6, work_p95() / 1e6, work / 1e6, shown_late(), g_held_back); }
    int pin = fps_pin(); if (pin >= 0) { if (g_step != pin) { g_step = pin; g_last_change = now; g_nshown = 0; } return; }
    if (now - g_last_change < 4000000000ll) return;
    int64_t p90 = work_pct(90); if (!p90) return;
    int nsteps = (int)(sizeof STEPS / sizeof *STEPS);
    if (g_step + 1 < nsteps && p90 > STEPS[g_step] * 105 / 100) {
        int late = shown_late(); if (late >= 0 && late < 7) g_held_back++;
        else {
            if (g_last_up && now - g_last_up < 10000000000ll) { g_up_hold = g_up_hold * 2 > 60000000000ll ? 60000000000ll : g_up_hold * 2; }
            g_step++; g_last_change = now; g_nwork = 0; g_nshown = 0; g_ok_frames = 0; LOG("[perf] adaptive frame rate: %.0f fps (frames p90 %.1f ms, late on screen %d of 64)", 1e9 / STEPS[g_step], p90 / 1e6, late); return;
        }
    }
    static int up_pct = -1; if (up_pct < 0) { const char *e = getenv("DM_ADAPT_UP_PCT"); up_pct = e && atoi(e) > 0 && atoi(e) < 100 ? atoi(e) : 90; }
    if (g_step > 0 && (up_pct == 90 ? p90 : work_pct(up_pct)) < STEPS[g_step - 1]) { if (++g_ok_frames > 120 && now - g_last_change >= g_up_hold) { g_step--; g_last_change = g_last_up = now; g_nwork = 0; g_nshown = 0; g_ok_frames = 0; LOG("[perf] adaptive frame rate: %.0f fps (frames p90 %.1f ms)", 1e9 / STEPS[g_step], p90 / 1e6); } }
    else g_ok_frames = 0;
}
static int64_t g_work_start, g_prev_present, g_pace, g_pq;
void dm_perf_frame_start(uint64_t pace_ns, uint64_t pq_ns) { g_work_start = perf_now(); g_pace = (int64_t)pace_ns; g_pq = (int64_t)pq_ns; }
void dm_perf_frame(double period) {
    int64_t p = period > 0 && period < 1 ? (int64_t)(period * 1e9) : 16666667, t = perf_now();
    (void)p; (void)gettid_;
    if (g_work_start) {
        int64_t w = g_prev_present ? t - g_prev_present - g_pace : t - g_work_start;
        int64_t pq = g_prev_present ? g_pq : 0, pace = g_step > 0 ? pq : 0;
        if (w > 0 && w < 250000000) adapt_frame(w - pace > 0 ? w - pace : 0, t);
        g_work_start = 0;
    }
    g_prev_present = t;
}
void dm_perf_window(ANativeWindow *w, double fps) { (void)w; if (fps > 0) LOG("[perf] window frame rate %.0f fps (the FIFO swapchain paces)", fps); }
__attribute__((weak)) double vk_refresh_ms(void) { return 0; }
__attribute__((weak)) double dm_nvn_present_hold_ms(void) { return 0; }
