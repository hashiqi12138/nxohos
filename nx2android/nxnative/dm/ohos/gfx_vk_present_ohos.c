// The window side of the Vulkan backend on HarmonyOS (the counterpart of dm/android/gfx_vk_present.c): an OHNativeWindow from the
// XComponent → VkSurfaceOHOS → swapchain, a present thread that blits the game's window texture into the swapchain image and presents
// (aspect-fit, no crop stretch), and the mtl_* entry points the NVN layer calls.
// Not here yet, and not needed for a first picture: the touch-gamepad overlay, the FPS counter, display-timing pacing (the FIFO swapchain
// paces instead), frame shots. Their callers are stubbed below so the shared code links unchanged.
#include "gfx_vk.h"
#include "nvn_mtl.h"
#include <native_window/external_window.h>
#include <math.h>
#include <string.h>
#include <time.h>

extern pthread_mutex_t g_vk_queue_mu;   // gfx_vk_core.c: the one queue
extern void dm_ohos_surface_size(int *, int *);   // front_ohos.c: the XComponent's surface size (OHOS has no ANativeWindow)
void dm_perf_frame(double period); void dm_perf_frame_cost(int64_t replay_ns, int64_t gpu_ns);

static VkSurfaceKHR g_surf; static VkSwapchainKHR g_swap;
static VkExtent2D g_sext; static VkFormat g_sfmt;
static uint32_t g_ni; static VkImage g_simg[8]; static VkImageView g_sview[8];
static VkFormat g_want;
static VkCommandPool g_ppool;
static VkSemaphore g_acq[8], g_done_sem[8]; static VkFence g_pfence[8]; static VkCommandBuffer g_pcb[8];
static uint32_t g_pi; static uint64_t g_presents;
static pthread_mutex_t g_present_mu = PTHREAD_MUTEX_INITIALIZER;   // the swapchain against the window going away
static _Atomic int g_lost;                                          // the window was destroyed: rebuild the swapchain on the next present

static void swap_destroy(void) {
    if (g_swap) { vkDeviceWaitIdle(V.dev); for (uint32_t i = 0; i < g_ni; i++) { if (g_sview[i]) vkDestroyImageView(V.dev, g_sview[i], NULL); g_sview[i] = VK_NULL_HANDLE; g_simg[i] = VK_NULL_HANDLE; }
        vkDestroySwapchainKHR(V.dev, g_swap, NULL); g_swap = VK_NULL_HANDLE; g_ni = 0; }
    if (g_surf) { vkDestroySurfaceKHR(V.inst, g_surf, NULL); g_surf = VK_NULL_HANDLE; }
    g_sext = (VkExtent2D){0, 0};
}

static int swap_make(void) {
    if (g_swap && !atomic_load(&g_lost)) return 1;
    swap_destroy(); atomic_store(&g_lost, 0);
    OHNativeWindow *win = dm_front_window();
    if (!win) return 0;
    VkSurfaceCreateInfoOHOS si = {VK_STRUCTURE_TYPE_SURFACE_CREATE_INFO_OHOS, NULL, 0, win};
    VkResult r = vkCreateSurfaceOHOS(V.inst, &si, NULL, &g_surf);
    if (r < 0) { VKRED_ONCE(70, "vkCreateSurfaceOHOS: %d", r); return 0; }
    VkSurfaceCapabilitiesKHR caps;
    if (vkGetPhysicalDeviceSurfaceCapabilitiesKHR(V.pd, g_surf, &caps) < 0) { VKRED_ONCE(71, "surface capabilities failed"); swap_destroy(); return 0; }
    uint32_t nf = 0; vkGetPhysicalDeviceSurfaceFormatsKHR(V.pd, g_surf, &nf, NULL);
    VkSurfaceFormatKHR fs[32]; if (nf > 32) nf = 32;
    if (!nf || vkGetPhysicalDeviceSurfaceFormatsKHR(V.pd, g_surf, &nf, fs) < 0) { VKRED_ONCE(72, "surface formats failed"); swap_destroy(); return 0; }
    // The format and its colour space are one pair the surface reports: asking for SRGB_NONLINEAR with a format that came with another colour space is a
    // request the Maleoon driver answers with abort(), not an error (measured by the demo, dm/ohos/demo_ohos.c), so take the pair as given.
    VkFormat want = g_want ? g_want : VK_FORMAT_R8G8B8A8_UNORM;
    g_sfmt = fs[0].format; VkColorSpaceKHR cs = fs[0].colorSpace;
    for (uint32_t i = 0; i < nf; i++) if (fs[i].format == want) { g_sfmt = want; cs = fs[i].colorSpace; }
    // Identity only: the present below is a straight blit with no rotation of its own, so a pre-rotated swapchain would show the frame on its
    // side. The device offers identity (0x1ff); anything else is refused loudly instead of presented wrongly.
    VkSurfaceTransformFlagBitsKHR tr = VK_SURFACE_TRANSFORM_IDENTITY_BIT_KHR;
    if (!(caps.supportedTransforms & VK_SURFACE_TRANSFORM_IDENTITY_BIT_KHR)) {
        VKRED_ONCE(80, "surface has no IDENTITY transform (0x%x): the present has no rotation of its own", caps.supportedTransforms);
        swap_destroy(); return 0;
    }
    int ww = 0, wh = 0; dm_ohos_surface_size(&ww, &wh);   // the XComponent's own size: OHOS has no ANativeWindow
    g_sext = caps.currentExtent.width != UINT32_MAX ? caps.currentExtent : (VkExtent2D){(uint32_t)ww, (uint32_t)wh};
    if (!g_sext.width || !g_sext.height) { VKRED_ONCE(73, "window has no size yet"); swap_destroy(); return 0; }
    // clamp into what the surface allows (the demo hit a 1x1 currentExtent on the first frame after the window appeared)
    if (g_sext.width < caps.minImageExtent.width) g_sext.width = caps.minImageExtent.width;
    if (g_sext.height < caps.minImageExtent.height) g_sext.height = caps.minImageExtent.height;
    if (g_sext.width > caps.maxImageExtent.width) g_sext.width = caps.maxImageExtent.width;
    if (g_sext.height > caps.maxImageExtent.height) g_sext.height = caps.maxImageExtent.height;
    // The XComponent's native window may not have its buffer geometry when the surface is handed over; without this the device reports SURFACE_LOST
    // and stops handing out images after the first frame (the M0 probe's finding, reproduced by demo_ohos.c).
    OH_NativeWindow_NativeWindowHandleOpt(win, SET_BUFFER_GEOMETRY, (int32_t)g_sext.width, (int32_t)g_sext.height);
    uint32_t nmodes = 0; vkGetPhysicalDeviceSurfacePresentModesKHR(V.pd, g_surf, &nmodes, NULL);
    VkPresentModeKHR pm[16]; if (nmodes > 16) nmodes = 16;
    VkPresentModeKHR mode = VK_PRESENT_MODE_FIFO_KHR;
    if (nmodes && vkGetPhysicalDeviceSurfacePresentModesKHR(V.pd, g_surf, &nmodes, pm) >= 0) {
        mode = pm[0];
        for (uint32_t i = 0; i < nmodes; i++) if (pm[i] == VK_PRESENT_MODE_FIFO_KHR) mode = pm[i];   // vsync: the refresh rate paces the game
    }
    uint32_t n = caps.minImageCount + 1; if (caps.maxImageCount && n > caps.maxImageCount) n = caps.maxImageCount;
    VkCompositeAlphaFlagBitsKHR alpha = (caps.supportedCompositeAlpha & VK_COMPOSITE_ALPHA_OPAQUE_BIT_KHR)
        ? VK_COMPOSITE_ALPHA_OPAQUE_BIT_KHR : VK_COMPOSITE_ALPHA_INHERIT_BIT_KHR;   // the device reports INHERIT only (alpha 0x8)
    // every frame clears and blits into the presented image, so a surface without TRANSFER_DST cannot be presented to at all
    if (!(caps.supportedUsageFlags & VK_IMAGE_USAGE_TRANSFER_DST_BIT)) {
        VKRED_ONCE(78, "surface cannot be a transfer destination (usage 0x%x): no way to blit the frame", caps.supportedUsageFlags);
        swap_destroy(); return 0;
    }
    VkImageUsageFlags usage = VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT;
    VkSwapchainCreateInfoKHR sc = {VK_STRUCTURE_TYPE_SWAPCHAIN_CREATE_INFO_KHR, NULL, 0, g_surf, n, g_sfmt, cs,
                                   g_sext, 1, usage,
                                   VK_SHARING_MODE_EXCLUSIVE, 0, NULL, tr, alpha, mode, VK_TRUE, VK_NULL_HANDLE};
    r = vkCreateSwapchainKHR(V.dev, &sc, NULL, &g_swap);
    if (r < 0) { VKRED_ONCE(74, "vkCreateSwapchainKHR: %d (%ux%u)", r, g_sext.width, g_sext.height); g_swap = VK_NULL_HANDLE; swap_destroy(); return 0; }
    vkGetSwapchainImagesKHR(V.dev, g_swap, &g_ni, NULL); if (g_ni > 8) g_ni = 8;
    vkGetSwapchainImagesKHR(V.dev, g_swap, &g_ni, g_simg);
    for (uint32_t i = 0; i < g_ni; i++) {
        VkImageViewCreateInfo vi = {VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO, NULL, 0, g_simg[i], VK_IMAGE_VIEW_TYPE_2D, g_sfmt,
                                    {VK_COMPONENT_SWIZZLE_IDENTITY, VK_COMPONENT_SWIZZLE_IDENTITY, VK_COMPONENT_SWIZZLE_IDENTITY, VK_COMPONENT_SWIZZLE_IDENTITY},
                                    {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1}};
        VKCHK(vkCreateImageView(V.dev, &vi, NULL, &g_sview[i]));
    }
    // The present slot objects live as long as the device, not the swapchain: made once, on the first swapchain. (The Android file makes them in the same
    // place for the same reason.) Without them vkQueueSubmit gets a NULL command buffer and the Maleoon driver faults on it — the frame's first submit.
    if (!g_ppool) {
        VkCommandPoolCreateInfo pci = {VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO, NULL, VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT, V.qf};
        VKCHK(vkCreateCommandPool(V.dev, &pci, NULL, &g_ppool));
        VkCommandBufferAllocateInfo ai = {VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO, NULL, g_ppool, VK_COMMAND_BUFFER_LEVEL_PRIMARY, 8};
        VKCHK(vkAllocateCommandBuffers(V.dev, &ai, g_pcb));
        for (int i = 0; i < 8; i++) {
            VkSemaphoreCreateInfo s = {VK_STRUCTURE_TYPE_SEMAPHORE_CREATE_INFO};
            VKCHK(vkCreateSemaphore(V.dev, &s, NULL, &g_acq[i]));
            VKCHK(vkCreateSemaphore(V.dev, &s, NULL, &g_done_sem[i]));
            VkFenceCreateInfo f = {VK_STRUCTURE_TYPE_FENCE_CREATE_INFO, NULL, VK_FENCE_CREATE_SIGNALED_BIT};
            VKCHK(vkCreateFence(V.dev, &f, NULL, &g_pfence[i]));
        }
    }
    VKLOG("window: %ux%u, %u images, format %d, present mode %d", g_sext.width, g_sext.height, g_ni, g_sfmt, mode);
    return 1;
}

// DM_VK_SHOT=<dir>: the game's own window texture, written as a PPM for the first three presents. A black screen then has one meaning instead of two: the
// frame the game produced (this file) is black, or it is not and the present path is at fault.
static VkBuffer g_sb; static VkDeviceMemory g_sbm; static void *g_sbp; static int g_shot_n, g_shot_total; static int g_shot_w, g_shot_h;
static int shot_on(void) {
    const char *d = getenv("DM_VK_SHOT"); if (!d || !*d || g_shot_n >= 10) return 0;
    const char *e = getenv("DM_VK_SHOT_EVERY"); int every = e && atoi(e) > 0 ? atoi(e) : 600;
    return g_shot_total <= 3 || (g_shot_total && g_shot_total % every == 0);   // the first frames and then one every N presents (default 600)
}
static void shot_setup(int w, int h) {
    if (g_sb) return;
    VkDeviceSize n = (VkDeviceSize)w * (VkDeviceSize)h * 4;
    VkBufferCreateInfo bi = {VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO, NULL, 0, n, VK_BUFFER_USAGE_TRANSFER_DST_BIT, VK_SHARING_MODE_EXCLUSIVE};
    VkMemoryRequirements rq; VkMemoryAllocateInfo ai;
    if (vkCreateBuffer(V.dev, &bi, NULL, &g_sb)) { g_sb = VK_NULL_HANDLE; return; }
    vkGetBufferMemoryRequirements(V.dev, g_sb, &rq);
    ai = (VkMemoryAllocateInfo){VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO, NULL, rq.size,
        vk_mem_type(rq.memoryTypeBits, VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT, 0)};
    if (vkAllocateMemory(V.dev, &ai, NULL, &g_sbm) || vkBindBufferMemory(V.dev, g_sb, g_sbm, 0) ||
        vkMapMemory(V.dev, g_sbm, 0, VK_WHOLE_SIZE, 0, &g_sbp)) { g_sb = VK_NULL_HANDLE; return; }
    g_shot_w = w; g_shot_h = h;
}
static void shot_write(void) {
    const char *d = getenv("DM_VK_SHOT"); if (!d || g_shot_n >= 10) return;
    char p[600]; snprintf(p, sizeof p, "%s/frame%d.ppm", d, g_shot_n);
    FILE *f = fopen(p, "wb"); if (!f) { VKLOG("shot: cannot open %s", p); return; }
    fprintf(f, "P6\n%d %d\n255\n", g_shot_w, g_shot_h);
    const unsigned char *s = (const unsigned char *)g_sbp;
    for (long i = 0; i < (long)g_shot_w * g_shot_h; i++) fwrite(s + i * 4, 1, 3, f);   // RGBA/BGRA → RGB (the channel order is read off the dump)
    fclose(f); VKLOG("shot: %s (%dx%d)", p, g_shot_w, g_shot_h); g_shot_n++;
}
static void barrier(VkCommandBuffer cb, VkImage img, VkImageLayout from, VkImageLayout to) {
    VkImageMemoryBarrier b = {VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER, NULL, VK_ACCESS_MEMORY_WRITE_BIT, VK_ACCESS_MEMORY_READ_BIT | VK_ACCESS_MEMORY_WRITE_BIT, from, to,
                              VK_QUEUE_FAMILY_IGNORED, VK_QUEUE_FAMILY_IGNORED, img, {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1}};
    vkCmdPipelineBarrier(cb, VK_PIPELINE_STAGE_ALL_COMMANDS_BIT, VK_PIPELINE_STAGE_ALL_COMMANDS_BIT, 0, 0, NULL, 0, NULL, 1, &b);
}

// one frame's commands into cb: clear the presented image, blit the game's frame into it, hand it to the compositor
static void record_frame(VkCommandBuffer cb, VkImage src, uint32_t idx, int cx, int cy, int cw, int ch, int dw, int dh, int dx, int dy) {
    vk_img_barrier_all(cb);   // after everything submitted before on this queue
    barrier(cb, g_simg[idx], VK_IMAGE_LAYOUT_UNDEFINED, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL);
    VkClearColorValue black = {{0, 0, 0, 1}}; VkImageSubresourceRange rg = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
    vkCmdClearColorImage(cb, g_simg[idx], VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, &black, 1, &rg);
    VkImageBlit b = {{VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1}, {{cx, cy, 0}, {cx + cw, cy + ch, 1}}, {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1}, {{dx, dy, 0}, {dx + dw, dy + dh, 1}}};
    if (vk_window_bottom_up()) { b.srcOffsets[0].y = cy + ch; b.srcOffsets[1].y = cy; }
    vkCmdBlitImage(cb, src, VK_IMAGE_LAYOUT_GENERAL, g_simg[idx], VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 1, &b, VK_FILTER_LINEAR);
    if (g_sb && shot_on()) {   // the game's frame as the game left it, before anything of ours touches it
        barrier(cb, src, VK_IMAGE_LAYOUT_GENERAL, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL);
        vkCmdFillBuffer(cb, g_sb, 0, (VkDeviceSize)g_shot_w * g_shot_h * 4, 0x5a5a5a5au);   // a copy that never ran leaves 0x5a behind, not a frame of zeros
        vk_img_barrier_all(cb);
        VkBufferImageCopy rc = {0, 0, 0, {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1}, {0, 0, 0}, {(uint32_t)g_shot_w, (uint32_t)g_shot_h, 1}};
        vkCmdCopyImageToBuffer(cb, src, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, g_sb, 1, &rc);
        barrier(cb, src, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, VK_IMAGE_LAYOUT_GENERAL);
    }
    barrier(cb, g_simg[idx], VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, VK_IMAGE_LAYOUT_PRESENT_SRC_KHR);
}

// one frame: the game's window texture into the swapchain image, letterboxed, then present
// Both waits are bounded: they run under g_present_mu, which the ArkTS thread takes to tear the swapchain down (dm_ohos_front_surface_lost), and the
// compositor stops handing out images as soon as the window goes away — an unbounded acquire would freeze the UI thread with the lock held.
#define PRESENT_WAIT_NS (100 * 1000 * 1000ULL)   // 100 ms: FIFO hands an image every vsync, so a miss means the window is gone
static double ms_since(const struct timespec *a) { struct timespec b; clock_gettime(CLOCK_MONOTONIC, &b); return (double)(b.tv_sec - a->tv_sec) * 1e3 + (double)(b.tv_nsec - a->tv_nsec) / 1e6; }
static void present_slow(const char *what, double a, double s, double p, double t) {
    static int n; if (n >= 30) return; n++;
    VKLOG("present: %s — acquire %.1f ms, submit %.1f, present %.1f, total %.1f", what, a, s, p, t);
}
static void present_window(vkimg_t *im, const int crop[4]) {
    static int dbg;   // the first frames step by step into the log: what a present-path fault would leave behind
    g_shot_total++;
    struct timespec t0; clock_gettime(CLOCK_MONOTONIC, &t0);
    int cx = crop ? crop[0] : 0, cy = crop ? crop[1] : 0, cw = crop && crop[2] ? crop[2] : (int)im->w, ch = crop && crop[3] ? crop[3] : (int)im->h;
    uint32_t k = g_pi++ % 8;
    if (vkWaitForFences(V.dev, 1, &g_pfence[k], VK_TRUE, PRESENT_WAIT_NS) == VK_TIMEOUT) return;   // the previous blit into this slot is still in flight
    VkFormat want = (im->fmt == VK_FORMAT_R8G8B8A8_SRGB || im->fmt == VK_FORMAT_B8G8R8A8_SRGB || im->fmt == VK_FORMAT_A8B8G8R8_SRGB_PACK32) ? VK_FORMAT_R8G8B8A8_SRGB : VK_FORMAT_R8G8B8A8_UNORM;
    if (want != g_want) { g_want = want; swap_destroy(); if (!swap_make()) return; }
    double s = fmin((double)g_sext.width / cw, (double)g_sext.height / ch);
    int dw = (int)(cw * s), dh = (int)(ch * s), dx = ((int)g_sext.width - dw) / 2, dy = ((int)g_sext.height - dh) / 2;
    uint32_t idx;
    VkResult r = vkAcquireNextImageKHR(V.dev, g_swap, PRESENT_WAIT_NS, g_acq[k], VK_NULL_HANDLE, &idx);
    double acq_ms = ms_since(&t0);
    if (r == VK_ERROR_OUT_OF_DATE_KHR) {
        if (dbg < 6) VKLOG("present step %d: out of date, rebuilding the swapchain", dbg);
        swap_destroy(); atomic_store(&g_lost, 1); if (!swap_make()) return;
        r = vkAcquireNextImageKHR(V.dev, g_swap, PRESENT_WAIT_NS, g_acq[k], VK_NULL_HANDLE, &idx);
    }
    if (r == VK_TIMEOUT || r == VK_NOT_READY) { present_slow("no image from the compositor", acq_ms, 0, 0, ms_since(&t0)); return; }   // no image: drop this frame (the semaphore was not signalled and the fence is untouched)
    if (r < 0) { VKRED_ONCE(75, "vkAcquireNextImageKHR: %d", r); return; }
    if (dbg < 6) { VKLOG("present step %d: acquired r=%d idx=%u of %u, swap %p", dbg, r, idx, g_ni, (void *)g_swap); }
    if (idx >= g_ni) {
        VKRED_ONCE(79, "swapchain image index %u of %u: rebuilding", idx, g_ni);
        // the acquire signalled g_acq[k]; a semaphore may not be waited-acquired twice, so consume it in a command-less submit first
        VkPipelineStageFlags w0 = VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT;
        VkSubmitInfo eat = {VK_STRUCTURE_TYPE_SUBMIT_INFO, NULL, 1, &g_acq[k], &w0, 0, NULL, 0, NULL};
        pthread_mutex_lock(&g_vk_queue_mu); vkQueueSubmit(V.q, 1, &eat, VK_NULL_HANDLE); pthread_mutex_unlock(&g_vk_queue_mu);
        swap_destroy(); atomic_store(&g_lost, 1); return;
    }
    vkResetFences(V.dev, 1, &g_pfence[k]);
    if (shot_on()) shot_setup((int)im->w, (int)im->h);
    VkCommandBuffer cb = g_pcb[k];
    VkCommandBufferBeginInfo bi = {VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO, NULL, VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT};
    if (dbg < 4) VKLOG("step %d: begin", dbg);
    vkBeginCommandBuffer(cb, &bi);
    record_frame(cb, im->img, idx, cx, cy, cw, ch, dw, dh, dx, dy);
    vkEndCommandBuffer(cb);
    if (dbg < 4) VKLOG("step %d: recorded", dbg);
    VkPipelineStageFlags ws = VK_PIPELINE_STAGE_TRANSFER_BIT;
    VkSubmitInfo si = {VK_STRUCTURE_TYPE_SUBMIT_INFO, NULL, 1, &g_acq[k], &ws, 1, &cb, 1, &g_done_sem[k]};
    pthread_mutex_lock(&g_vk_queue_mu);
    if (dbg < 4) VKLOG("step %d: submitting", dbg);
    struct timespec tq; clock_gettime(CLOCK_MONOTONIC, &tq);
    if (vkQueueSubmit(V.q, 1, &si, g_pfence[k])) {
        // A failed submit consumed neither the acquire semaphore nor the fence. One command-less submit that waits the semaphore and signals the
        // fence puts both back into a usable state; without it the next frame waits forever on this fence and re-acquires a signalled semaphore.
        VkSubmitInfo fix = {VK_STRUCTURE_TYPE_SUBMIT_INFO, NULL, 1, &g_acq[k], &ws, 0, NULL, 0, NULL};
        vkQueueSubmit(V.q, 1, &fix, g_pfence[k]);
        pthread_mutex_unlock(&g_vk_queue_mu); VKRED_ONCE(76, "present submit failed"); swap_destroy(); atomic_store(&g_lost, 1); return;
    }
    VkPresentInfoKHR pi = {VK_STRUCTURE_TYPE_PRESENT_INFO_KHR, NULL, 1, &g_done_sem[k], 1, &g_swap, &idx, NULL};
    double sub_ms = ms_since(&tq);
    if (dbg < 6) VKLOG("present step %d: submitted", dbg);
    r = vkQueuePresentKHR(V.q, &pi);
    { double prs_ms = ms_since(&tq) - sub_ms, tot = ms_since(&t0);
      if (tot > 50.0) present_slow(r == 0 ? "slow" : "present failed", acq_ms, sub_ms, prs_ms, tot); }
    if (dbg < 6) { VKLOG("present step %d: presented r=%d", dbg, r); dbg++; }
    if (g_sb && shot_on() && vkWaitForFences(V.dev, 1, &g_pfence[k], VK_TRUE, PRESENT_WAIT_NS) == VK_SUCCESS) shot_write();
    pthread_mutex_unlock(&g_vk_queue_mu);
    if (r == VK_ERROR_OUT_OF_DATE_KHR) { atomic_store(&g_lost, 1); }
}

// The present thread: mtl_present only queues a frame and returns, so the game thread never waits for a vsync inside the swapchain.
#define PQ_N 4
static struct { vkimg_t *im; int crop[4], has_crop; uint64_t n; } g_pq[PQ_N];
static uint64_t g_pq_head, g_pq_tail, g_pq_done;
static pthread_mutex_t g_pq_mu = PTHREAD_MUTEX_INITIALIZER; static pthread_cond_t g_pq_cv = PTHREAD_COND_INITIALIZER;
static _Atomic int g_redraw;
void dm_overlay_redraw(void) { atomic_store(&g_redraw, 1); pthread_mutex_lock(&g_pq_mu); pthread_cond_broadcast(&g_pq_cv); pthread_mutex_unlock(&g_pq_mu); }
static void *present_thread(void *arg) {
    (void)arg; pthread_setname_np(pthread_self(), "vk present");
    typeof(g_pq[0]) last = {0};
    for (;;) {
        pthread_mutex_lock(&g_pq_mu);
        while (g_pq_tail == g_pq_head && !atomic_load(&g_redraw)) pthread_cond_wait(&g_pq_cv, &g_pq_mu);
        atomic_store(&g_redraw, 0);
        if (g_pq_tail == g_pq_head) {   // a redraw of the last frame (the pause menu), no new one
            pthread_mutex_unlock(&g_pq_mu);
            if (last.im) { pthread_mutex_lock(&g_present_mu); if (swap_make()) present_window(last.im, last.has_crop ? last.crop : NULL); pthread_mutex_unlock(&g_present_mu); }
            continue;
        }
        typeof(g_pq[0]) e = g_pq[g_pq_tail % PQ_N];
        pthread_mutex_unlock(&g_pq_mu);
        if (e.im) { last = e; pthread_mutex_lock(&g_present_mu); if (swap_make()) present_window(e.im, e.has_crop ? e.crop : NULL); pthread_mutex_unlock(&g_present_mu); }
        pthread_mutex_lock(&g_pq_mu); g_pq_tail++; g_pq_done = e.n; pthread_cond_broadcast(&g_pq_cv); pthread_mutex_unlock(&g_pq_mu);
    }
    return NULL;
}
static void present_thread_start(void) { pthread_t t; pthread_create(&t, NULL, present_thread, NULL); pthread_detach(t); }
static uint64_t present_enqueue(vkimg_t *im, const int crop[4], uint64_t n) {
    static int sync = -1; static pthread_once_t once = PTHREAD_ONCE_INIT;
    if (sync < 0) sync = getenv("DM_VK_SYNC_PRESENT") != NULL;
    if (sync || !dm_front_window()) {   // headless (nothing to show) or presenting on this thread
        if (sync && im) { pthread_mutex_lock(&g_present_mu); if (swap_make()) present_window(im, crop); pthread_mutex_unlock(&g_present_mu); }
        pthread_mutex_lock(&g_pq_mu); if (g_pq_tail == g_pq_head) g_pq_done = n; pthread_mutex_unlock(&g_pq_mu);
        return n;
    }
    pthread_once(&once, present_thread_start);
    pthread_mutex_lock(&g_pq_mu);
    while (g_pq_head - g_pq_tail >= PQ_N) pthread_cond_wait(&g_pq_cv, &g_pq_mu);
    typeof(g_pq[0]) *e = &g_pq[g_pq_head % PQ_N]; e->im = im; e->n = n; e->has_crop = crop != NULL; if (crop) memcpy(e->crop, crop, sizeof e->crop);
    g_pq_head++; pthread_cond_broadcast(&g_pq_cv);
    pthread_mutex_unlock(&g_pq_mu);
    return n;
}

void vk_window_lost(void) { pthread_mutex_lock(&g_present_mu); swap_destroy(); atomic_store(&g_lost, 1); pthread_mutex_unlock(&g_present_mu); }
// the XComponent's surface changed size (or was recreated): rebuild the swapchain on the next present
void dm_ohos_window_resized(void) { atomic_store(&g_lost, 1); pthread_mutex_lock(&g_present_mu); swap_destroy(); pthread_mutex_unlock(&g_present_mu); }

uint64_t mtl_present(const tex_t *t, double period, const int crop[4]) {
    vk_init();
    { uint64_t vk_replay_ns(void); double vk_gpu_ms_recent(void);
      static uint64_t last_exec; uint64_t ex = vk_replay_ns(), rp = ex - last_exec; last_exec = ex;
      double g = vk_gpu_ms_recent(); dm_perf_frame_cost((int64_t)rp, g > 0 ? (int64_t)(g * 1e6) : 0); }
    dm_perf_frame(period);
    uint64_t n = ++g_presents;
    vkimg_t *im = vk_tex(t);
    const char *pt = getenv("DM_VK_PRESENT_TID");   // debug: bypass the game's final fragment and present a registered source directly
    if (pt && *pt) { const view_t *v = NULL; const tex_t *st = NULL; if (vk_tex_by_id(atoi(pt), &v, &st) && st) { vkimg_t *sim = vk_tex(st);
        VkFormatProperties fp; vkGetPhysicalDeviceFormatProperties(V.pd, sim ? sim->fmt : VK_FORMAT_UNDEFINED, &fp);
        if (sim && sim->aspect == VK_IMAGE_ASPECT_COLOR_BIT && sim->samples == 1 && !vk_fmt(sim->nvn_format)->is_int &&
            (fp.optimalTilingFeatures & (VK_FORMAT_FEATURE_BLIT_SRC_BIT | VK_FORMAT_FEATURE_SAMPLED_IMAGE_FILTER_LINEAR_BIT)) == (VK_FORMAT_FEATURE_BLIT_SRC_BIT | VK_FORMAT_FEATURE_SAMPLED_IMAGE_FILTER_LINEAR_BIT)) { im = sim; crop = NULL; } } }
    if (n % 600 == 0 && im) {   // on the replay/present caller, not the asynchronous window thread: g_vkst is plain replay-thread state
        extern struct { uint64_t draws, skip_prog, skip_rt, skip_pipe, passes, folded, dispatches, pipes_built, rt_kept, ns_exec, ns_desc, ns_pipe, max_pass_draws; } g_vkst;
        VkFormatProperties wfp; vkGetPhysicalDeviceFormatProperties(V.pd, im->fmt, &wfp);
        VKLOG("replay up to present %llu: draws %llu, skipped (no program %llu, no target %llu, no pipeline %llu), passes %llu, dispatches %llu; window texture %ux%u fmt %d layout %d pool %#llx+%#llx image %p features %#x",
              (unsigned long long)n, (unsigned long long)g_vkst.draws, (unsigned long long)g_vkst.skip_prog, (unsigned long long)g_vkst.skip_rt, (unsigned long long)g_vkst.skip_pipe,
              (unsigned long long)g_vkst.passes, (unsigned long long)g_vkst.dispatches, im->w, im->h, im->fmt, im->layout, (unsigned long long)im->pool, (unsigned long long)im->off, (const void *)im, wfp.optimalTilingFeatures);
        VKLOG("descriptor limits: maxBoundDescriptorSets %u, maxDescriptorSetUniformBuffersDynamic %u, maxUniformBufferRange %u", V.props.limits.maxBoundDescriptorSets, V.props.limits.maxDescriptorSetUniformBuffersDynamic, V.props.limits.maxUniformBufferRange);
    }
    { void vk_pass_dump_poll(uint64_t done); uint64_t vk_fence_done(void); vk_pass_dump_poll(vk_fence_done()); }   // same present-side dump lifecycle as gfx_vk_present.c
    if (!im) { VKRED_ONCE(77, "present: no image for the window texture"); return present_enqueue(NULL, NULL, n); }
    return present_enqueue(im, crop, n);
}
void mtl_present_wait(uint64_t tk) {   // NVN acquire: the window texture's last present has been blitted, the game may draw into it again
    if (!tk) return;
    pthread_mutex_lock(&g_pq_mu);
    while (g_pq_done < tk) pthread_cond_wait(&g_pq_cv, &g_pq_mu);
    pthread_mutex_unlock(&g_pq_mu);
}
double mtl_presented_time(uint64_t n) { (void)n; return 0; }   // no display timing here; the governor's late check stays off
int mtl_link_active(void) { return 0; }
int mtl_link_wait_tick(int vsyncs, uint64_t timeout_ns) { (void)vsyncs; (void)timeout_ns; return 0; }
int mtl_has_window(void) { return dm_front_window() != NULL; }
int mtl_display_hz(void) { return 60; }
int mtl_fps_target(void) { return DM_GAME_FPS_TARGET > 0 ? DM_GAME_FPS_TARGET : 60; }
void mtl_xv_window_tex(uint64_t serial) { (void)serial; }
void mtl_prog_release(prog_t *p) { (void)p; }
uint32_t mtl_present_qos(void) { return 0; }   // only for the "[nvn] thread policy" log line

// ---- no overlay on this port yet: the shared code's overlay hooks answer "nothing to draw" ----
int dm_overlay_visible(void) { return 0; }
int dm_overlay_fps(void) { return 0; }
int dm_menu_state(void) { return -1; }
void dm_save_note(const char *s) { (void)s; }
