// OHOS backend shim: the part of main's NVN backend API (nvn_mtl.h / nvn_obj.h, implemented upstream in the Metal nvn_mtl.m)
// that the additive Vulkan backend (dm/gfx/vk) does not provide. Stats / capture / pipeline-archive / present-shape queries are
// inert here; the core calls forward to the Vulkan backend's existing entry points. The texture/program pin and queue-state
// calls are no-ops for bring-up (the Vulkan backend resolves descriptors immediately and replays one queue) — verify on device.
#include <stdint.h>
#include <stddef.h>
#include <stdatomic.h>
#include <sys/mman.h>
#include "nvn_obj.h"
#include "nvn_mtl.h"
#include "nvn_capture.h"
#include "hle_movie_avc.h"

// ---- globals the Vulkan backend references (gfx_vk_core.c, gfx_vk_shader.c) ----
uint64_t g_nvn_discards[2];
void (*g_nvn_on_shader)(const uint8_t *, const void *, uint64_t, uint32_t);

// ---- memory pool ----
void mtl_pool_addressed(const void *mem, uint64_t size) { (void)mem; (void)size; }
void mtl_pool_replace(const void *const *del, int ndel, const void *const *add, const uint64_t *size, int nadd) {
    for (int i = 0; i < ndel; i++) mtl_pool_del(del[i]);
    for (int i = 0; i < nadd; i++) mtl_pool_add(add[i], size[i]);
}
void mtl_sp_phys_gone(const void *mem, uint64_t size) { (void)mem; (void)size; }
int mtl_addr_known(uint64_t addr, uint64_t len) { void *buf; uint64_t off; return mtl_addr(addr, len, &buf, &off); }

// ---- submit / queue / fence ----
void mtl_prepare(void) {}
void mtl_queue_activate(uint64_t serial) { (void)serial; }
void mtl_queue_forget(uint64_t serial) { (void)serial; }
uint64_t mtl_completed_fence(void) { return mtl_last_fence(); }
int mtl_wait_acq(uint64_t fence, uint64_t timeout_ns) { return mtl_wait(fence, timeout_ns); }
void mtl_arch_save(int wait_ms, int ending) { (void)wait_ms; (void)ending; }
void mtl_set_queue_origin_lower_left(int on) { (void)on; }

// ---- present ----
void mtl_present_layer(uint32_t w, uint32_t h) { (void)w; (void)h; }
void mtl_present_screen(uint32_t w, uint32_t h) { (void)w; (void)h; }
uint64_t mtl_present_screen_wait(double period) { (void)period; return 0; }
void mtl_present_wait_texture(uint64_t ticket, uint64_t copy) { (void)copy; mtl_present_wait(ticket); }
void mtl_present_progress(uint64_t *queued, uint64_t *done) { if (queued) *queued = 0; if (done) *done = 0; }
static _Atomic uint64_t g_rec_frame;
void mtl_present_recorded(uint64_t frame) { uint64_t w = atomic_load(&g_rec_frame); while (frame > w && !atomic_compare_exchange_weak(&g_rec_frame, &w, frame)) {} }

// ---- display link ----
uint64_t mtl_link_drain_take_ns(void) { return 0; }
int mtl_link_rate(void) { return 60; }
int mtl_link_stalled(void) { return 0; }
int mtl_link_tick(uint64_t n, double *target, double *lead_ms, int *ticks, double *gap_ms) {
    (void)n; if (target) *target = 0; if (lead_ms) *lead_ms = 0; if (ticks) *ticks = 0; if (gap_ms) *gap_ms = 0; return 0;
}
int mtl_link_wait_ticks(int ticks, uint64_t timeout_ns) { (void)ticks; (void)timeout_ns; return 1; }

// ---- textures / programs / samplers ----
void mtl_set_samp_pool_n(int n) { (void)n; }
void mtl_tex_pin(uint64_t serial) { (void)serial; }
void mtl_tex_unpin(uint64_t serial) { (void)serial; }
void mtl_reg_image(int id, const tex_t *t, const view_t *v) { (void)id; (void)t; (void)v; }
void mtl_tex_gpu_written(const tex_t *t) { (void)t; }
void mtl_tex_linear_cpu_written(const tex_t *t) { (void)t; }
void mtl_prog_unpin(prog_t *p) { (void)p; }
void mtl_prog_snapshot(prog_t *copy, const prog_t *source) { (void)copy; (void)source; }
void mtl_prog_prefetch(const prog_t *p) { (void)p; }
void mtl_prog_forget(const prog_t *p) { (void)p; }

// ---- snapshots / probes / prebuild ----
int mtl_snap_on(void) { return 0; }
uint64_t mtl_snap_acquire_wait(void) { return 0; }
int mtl_probe_ramp_on(void) { return 0; }
int mtl_pso_prebuild_tool(void) { return 0; }
void mtl_ab_gpu(uint64_t f, double gpu_ms, double frame_ms) { (void)f; (void)gpu_ms; (void)frame_ms; }

// ---- stats (inert) ----
unsigned mtl_cpu_tex_count(void) { return 0; }
uint64_t mtl_pkg_edge_skips(void) { return 0; }
void mtl_sm_stats(uint64_t *a, uint64_t *b, uint64_t *c) { *a = *b = *c = 0; }
void mtl_fb_stats(uint64_t *a, uint64_t *b, uint64_t *c, uint64_t *d) { *a = *b = *c = *d = 0; }
void mtl_dispatch_barrier_stats(uint64_t *a, uint64_t *b, uint64_t *c, uint64_t *d) { *a = *b = *c = *d = 0; }
void mtl_hybrid_stats(uint64_t *a, uint64_t *b, uint64_t *c) { *a = *b = *c = 0; }
void mtl_gsb_stats(uint64_t *a, uint64_t *b) { *a = *b = 0; }
void mtl_tf_stats(uint64_t v[4]) { for (int i = 0; i < 4; i++) v[i] = 0; }
void mtl_pool_stats(uint64_t s[6]) { for (int i = 0; i < 6; i++) s[i] = 0; }
void mtl_cshoist_stats(uint64_t *a, uint64_t *b, uint64_t *c) { *a = *b = 0; for (int i = 0; i < 8; i++) c[i] = 0; }
void mtl_geohoist_stats(uint64_t *a, uint64_t *b, uint64_t *c) { *a = *b = 0; for (int i = 0; i < 6; i++) c[i] = 0; }
void mtl_geomesh_stats(uint64_t *a, uint64_t *b, uint64_t *c, uint64_t *d, uint64_t *e) { *a = *b = *c = *d = 0; for (int i = 0; i < 4; i++) e[i] = 0; }
void mtl_pso_line(char *out, size_t n) { if (n) out[0] = 0; }

// ---- capture (inert) ----
void nvn_cap_submit(int n, const uint64_t *handles) { (void)n; (void)handles; }
void nvn_cap_present(const tex_t *t, const int crop[4]) { (void)t; (void)crop; }
void nvn_cap_queue(uint64_t serial, int waits) { (void)serial; (void)waits; }

// ---- dirty-tracking wrappers (the core lives in nvn_dirty.c; these are the backend's retirement / defrag hooks) ----
void nvn_dirty_release(const void *addr, size_t len) {
    if (!len) return;
    uintptr_t a = (uintptr_t)addr, p = a & ~(uintptr_t)4095, e = (a + len + 4095) & ~(uintptr_t)4095;
    mprotect((void *)p, e - p, PROT_READ | PROT_WRITE);   // retirement restores RW (the fault handler also restores on write)
}
uint64_t nvn_dirty_released(void) { return 0; }
int nvn_dirty_runs(void) { return 0; }
int nvn_dirty_defrag(int (*remap_ro)(void *, size_t)) { (void)remap_ro; return 0; }

// ---- host front end / movie / debug hooks ----
int front_ctrl_support(int min, int max) { (void)min; (void)max; return 0; }   // no controller applet on OHOS
int mavc_bt709(const mavc *a, int h) { (void)a; (void)h; return 0; }            // AVC decoder is M2/M3 work; hle_movie.c gets no BT.709 hint
void dm_gfx_restop(void) {}
void dm_gfx_bufsnap(const char *key, uint64_t a, size_t n, const char *path) { (void)key; (void)a; (void)n; (void)path; }

// ---- Objective-C runtime (no ObjC on OHOS; the shared sources' autorelease pools are inert) ----
void *objc_autoreleasePoolPush(void) { return NULL; }
void objc_autoreleasePoolPop(void *p) { (void)p; }