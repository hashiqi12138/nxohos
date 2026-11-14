// The M1.3 self-test: an animated Vulkan scene in the game's window, with no game and no NVN involved.
// It exists so the window chain can be judged on the device before any game file is present — XComponent → OHNativeWindow → VkSurfaceOHOS →
// swapchain → present — and so a regression there is not confused with a regression in the replay. Bars march across the screen at 60 fps.
//   startDemo() from ArkTS, after setSurfaceId(); stopDemo() when the page goes away.
#include <dlfcn.h>
#include <execinfo.h>
#include <math.h>
#include <native_window/external_window.h>
#include <pthread.h>
#include <signal.h>
#include <ucontext.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <vulkan/vulkan.h>
#include <vulkan/vulkan_ohos.h>
#include "dm.h"

OHNativeWindow *dm_front_window(void) __attribute__((weak));   // front_ohos.c
static OHNativeWindow *win_of(void) { return dm_front_window ? dm_front_window() : NULL; }
extern void dm_ohos_surface_size(int *, int *);

static atomic_int g_stop;
static atomic_int g_run;   // the scene thread is alive (set by start, cleared by the wrapper below) — one at a time

// gfx_vk.h gives the Vulkan backend a global function *pointer* per entry point, named exactly like the entry point (`extern PFN_vkX vkX`).
// A plain call to vkX from inside libdmhost.so therefore binds to that pointer variable's address — a data address — and died with
// SIG 11 (pc == fault address == an address in libdmhost.so, measured on the device). Resolve the entry points from libvulkan instead and
// call through p_*.
#define DM_VK_ENTRIES(X) \
    X(vkCreateInstance) X(vkEnumeratePhysicalDevices) X(vkGetPhysicalDeviceQueueFamilyProperties) \
    X(vkGetPhysicalDeviceProperties) X(vkCreateDevice) X(vkGetDeviceQueue) \
    X(vkGetPhysicalDeviceSurfaceSupportKHR) X(vkGetPhysicalDeviceSurfaceFormatsKHR) \
    X(vkGetPhysicalDeviceSurfaceCapabilitiesKHR) X(vkCreateSwapchainKHR) X(vkGetSwapchainImagesKHR) \
    X(vkCreateRenderPass) X(vkCreateCommandPool) X(vkAllocateCommandBuffers) \
    X(vkCreateImageView) X(vkCreateFramebuffer) X(vkAcquireNextImageKHR) \
    X(vkResetCommandBuffer) X(vkBeginCommandBuffer) X(vkCmdBeginRenderPass) \
    X(vkCmdClearAttachments) X(vkCmdEndRenderPass) X(vkEndCommandBuffer) \
    X(vkQueueSubmit) X(vkQueuePresentKHR) X(vkQueueWaitIdle) X(vkDeviceWaitIdle) \
    X(vkDestroyFramebuffer) X(vkDestroyImageView) X(vkDestroySwapchainKHR) \
    X(vkDestroyRenderPass) X(vkDestroyCommandPool) X(vkDestroySurfaceKHR)

#define DM_VK_DECL(f) static PFN_##f p_##f;
DM_VK_ENTRIES(DM_VK_DECL)
#undef DM_VK_DECL

static int vk_entries(void) {
    static int ok;
    if (ok) return 1;
    void *h = dlopen("libvulkan.so", RTLD_NOW | RTLD_LOCAL);
    if (!h) h = dlopen("libvulkan.so.1", RTLD_NOW | RTLD_LOCAL);
    if (!h) { LOG("[demo] libvulkan: %s", dlerror()); return 0; }
#define DM_VK_LOAD(f) p_##f = (PFN_##f)dlsym(h, #f); if (!p_##f) { LOG("[demo] libvulkan lacks %s", #f); return 0; }
    DM_VK_ENTRIES(DM_VK_LOAD)
#undef DM_VK_LOAD
    ok = 1;
    return 1;
}

#define vkCreateInstance p_vkCreateInstance
#define vkEnumeratePhysicalDevices p_vkEnumeratePhysicalDevices
#define vkGetPhysicalDeviceQueueFamilyProperties p_vkGetPhysicalDeviceQueueFamilyProperties
#define vkGetPhysicalDeviceProperties p_vkGetPhysicalDeviceProperties
#define vkCreateDevice p_vkCreateDevice
#define vkGetDeviceQueue p_vkGetDeviceQueue
#define vkGetPhysicalDeviceSurfaceSupportKHR p_vkGetPhysicalDeviceSurfaceSupportKHR
#define vkGetPhysicalDeviceSurfaceFormatsKHR p_vkGetPhysicalDeviceSurfaceFormatsKHR
#define vkGetPhysicalDeviceSurfaceCapabilitiesKHR p_vkGetPhysicalDeviceSurfaceCapabilitiesKHR
#define vkCreateSwapchainKHR p_vkCreateSwapchainKHR
#define vkGetSwapchainImagesKHR p_vkGetSwapchainImagesKHR
#define vkCreateRenderPass p_vkCreateRenderPass
#define vkCreateCommandPool p_vkCreateCommandPool
#define vkAllocateCommandBuffers p_vkAllocateCommandBuffers
#define vkCreateImageView p_vkCreateImageView
#define vkCreateFramebuffer p_vkCreateFramebuffer
#define vkAcquireNextImageKHR p_vkAcquireNextImageKHR
#define vkResetCommandBuffer p_vkResetCommandBuffer
#define vkBeginCommandBuffer p_vkBeginCommandBuffer
#define vkCmdBeginRenderPass p_vkCmdBeginRenderPass
#define vkCmdClearAttachments p_vkCmdClearAttachments
#define vkCmdEndRenderPass p_vkCmdEndRenderPass
#define vkEndCommandBuffer p_vkEndCommandBuffer
#define vkQueueSubmit p_vkQueueSubmit
#define vkQueuePresentKHR p_vkQueuePresentKHR
#define vkQueueWaitIdle p_vkQueueWaitIdle
#define vkDeviceWaitIdle p_vkDeviceWaitIdle
#define vkDestroyFramebuffer p_vkDestroyFramebuffer
#define vkDestroyImageView p_vkDestroyImageView
#define vkDestroySwapchainKHR p_vkDestroySwapchainKHR
#define vkDestroyRenderPass p_vkDestroyRenderPass
#define vkDestroyCommandPool p_vkDestroyCommandPool
#define vkDestroySurfaceKHR p_vkDestroySurfaceKHR

// A fault inside the Vulkan loader or the driver leaves nothing in hilog (the process just dies with signal 11 and the fault log is not
// readable from the console's shell), so the demo names the faulting address and the symbol it falls in before the process goes down.
static void demo_crash(int sig, siginfo_t *si, void *uc) {
    ucontext_t *u = (ucontext_t *)uc;
    void *pc = (void *)u->uc_mcontext.pc;
    Dl_info di;
    if (dladdr(pc, &di) && di.dli_sname)
        LOG("[demo] SIG %d at %s+0x%lx (addr %p)", sig, di.dli_sname, (long)((char *)pc - (char *)di.dli_saddr), si->si_addr);
    else
        LOG("[demo] SIG %d at pc %p (+0x%lx in %s, addr %p)", sig, pc,
            (long)((char *)pc - (char *)di.dli_fbase), di.dli_fname ? di.dli_fname : "?", si->si_addr);
    void *bt[24];
    int n = backtrace(bt, 24);
    for (int i = 0; i < n; i++) {
        Dl_info d;
        if (dladdr(bt[i], &d) && d.dli_fname)
            LOG("[demo] #%d %s+0x%lx", i, d.dli_fname, (long)((char *)bt[i] - (char *)d.dli_fbase));
        else
            LOG("[demo] #%d %p", i, bt[i]);
    }
    signal(sig, SIG_DFL);
    raise(sig);
}
static void demo_crash_watch(void) {
    struct sigaction sa;
    memset(&sa, 0, sizeof sa);
    sa.sa_sigaction = demo_crash;
    sa.sa_flags = SA_SIGINFO;
    sigaction(SIGSEGV, &sa, NULL); sigaction(SIGBUS, &sa, NULL);
    sigaction(SIGILL, &sa, NULL); sigaction(SIGABRT, &sa, NULL);
}

static void *demo_body(void *arg) {
    (void)arg;
    demo_crash_watch();
    if (!vk_entries()) return NULL;
    LOG("[demo] build 5, thread up, window %p", (void *)win_of());   // the number proves which .so the device is running
    VkApplicationInfo ai = {VK_STRUCTURE_TYPE_APPLICATION_INFO, NULL, "dmhost demo", 1, "dmhost", 1, VK_API_VERSION_1_1};
    const char *iext[] = {VK_KHR_SURFACE_EXTENSION_NAME, VK_OHOS_SURFACE_EXTENSION_NAME};
    VkInstanceCreateInfo ici = {VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, NULL, 0, &ai, 0, NULL, 2, iext};
    VkInstance inst;
    VkResult r = vkCreateInstance(&ici, NULL, &inst);
    LOG("[demo] vkCreateInstance -> %d", r);
    if (r < 0) { LOG("[demo] vkCreateInstance: %d", r); return NULL; }
    PFN_vkCreateSurfaceOHOS create_surface = (PFN_vkCreateSurfaceOHOS)vkGetInstanceProcAddr(inst, "vkCreateSurfaceOHOS");
    if (!create_surface) { LOG("[demo] VK_OHOS_surface is not there"); return NULL; }
    uint32_t npd = 0; vkEnumeratePhysicalDevices(inst, &npd, NULL);
    if (!npd) { LOG("[demo] no Vulkan device"); return NULL; }
    VkPhysicalDevice *pds = calloc(npd, sizeof *pds); vkEnumeratePhysicalDevices(inst, &npd, pds);
    VkPhysicalDevice pd = VK_NULL_HANDLE; uint32_t qf = 0;
    for (uint32_t i = 0; i < npd && pd == VK_NULL_HANDLE; i++) {
        uint32_t nq = 0; vkGetPhysicalDeviceQueueFamilyProperties(pds[i], &nq, NULL);
        VkQueueFamilyProperties *qq = calloc(nq, sizeof *qq); vkGetPhysicalDeviceQueueFamilyProperties(pds[i], &nq, qq);
        for (uint32_t j = 0; j < nq; j++) if (qq[j].queueFlags & VK_QUEUE_GRAPHICS_BIT) { pd = pds[i]; qf = j; break; }
        free(qq);
    }
    free(pds);
    if (pd == VK_NULL_HANDLE) { LOG("[demo] no graphics queue"); return NULL; }
    VkPhysicalDeviceProperties pp; vkGetPhysicalDeviceProperties(pd, &pp);
    LOG("[demo] device: %s (Vulkan %u.%u.%u)", pp.deviceName, VK_VERSION_MAJOR(pp.apiVersion), VK_VERSION_MINOR(pp.apiVersion), VK_VERSION_PATCH(pp.apiVersion));
    float prio = 1.0f; VkDeviceQueueCreateInfo qci = {VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO, NULL, 0, qf, 1, &prio};
    // Without VK_KHR_swapchain enabled on the device, vkCreateSwapchainKHR is undefined: the Maleoon driver aborts instead of returning an
    // error (SIGABRT right after the surface capabilities, measured).
    const char *dext[] = {VK_KHR_SWAPCHAIN_EXTENSION_NAME};
    VkDeviceCreateInfo dci = {VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO, NULL, 0, 1, &qci, 0, NULL, 1, dext, NULL};
    VkDevice dev; if (vkCreateDevice(pd, &dci, NULL, &dev) < 0) { LOG("[demo] vkCreateDevice failed"); return NULL; }
    VkQueue q; vkGetDeviceQueue(dev, qf, 0, &q);

    VkSurfaceKHR surf = VK_NULL_HANDLE; VkSwapchainKHR swap = VK_NULL_HANDLE;
    uint32_t ni = 0; VkImage imgs[8]; VkImageView views[8]; VkFramebuffer fbs[8]; VkRenderPass rp = VK_NULL_HANDLE;
    VkExtent2D ext = {0, 0}; VkFormat fmt = VK_FORMAT_UNDEFINED; VkCommandPool pool = VK_NULL_HANDLE; VkCommandBuffer cbs[8];
    uint64_t frames = 0; int bad = 0; int waits = 0;
    while (!atomic_load(&g_stop) && bad < 3) {
        if (!win_of()) { usleep(50 * 1000); continue; }
        if (!swap) {   // (re)build everything that depends on the window
            LOG("[demo] creating VkSurfaceOHOS for window %p", (void *)win_of());
            VkSurfaceCreateInfoOHOS sci = {VK_STRUCTURE_TYPE_SURFACE_CREATE_INFO_OHOS, NULL, 0, win_of()};
            r = create_surface(inst, &sci, NULL, &surf);
            if (r < 0) { LOG("[demo] vkCreateSurfaceOHOS: %d", r); bad++; usleep(100 * 1000); continue; }
            VkBool32 sup = 0; vkGetPhysicalDeviceSurfaceSupportKHR(pd, qf, surf, &sup);
            if (!sup) { LOG("[demo] the queue cannot present to this surface"); return NULL; }
            uint32_t nf = 0; vkGetPhysicalDeviceSurfaceFormatsKHR(pd, surf, &nf, NULL);
            VkSurfaceFormatKHR fs[32]; if (!nf || nf > 32) { LOG("[demo] no surface formats"); return NULL; }
            vkGetPhysicalDeviceSurfaceFormatsKHR(pd, surf, &nf, fs);
            // The format and its colour space are one pair the surface reports: asking for SRGB_NONLINEAR with a format that came with
            // another colour space is a request the Maleoon driver does not answer with an error (it aborts), so take the pair as given.
            fmt = fs[0].format; VkColorSpaceKHR cs = fs[0].colorSpace;
            for (uint32_t i = 0; i < nf; i++) if (fs[i].format == VK_FORMAT_R8G8B8A8_UNORM) { fmt = fs[i].format; cs = fs[i].colorSpace; }
            VkSurfaceCapabilitiesKHR caps; vkGetPhysicalDeviceSurfaceCapabilitiesKHR(pd, surf, &caps);
            int w = 0, h = 0; dm_ohos_surface_size(&w, &h);
            ext = caps.currentExtent.width != UINT32_MAX ? caps.currentExtent : (VkExtent2D){(uint32_t)w, (uint32_t)h};
            if (!ext.width || !ext.height) { vkDestroySurfaceKHR(inst, surf, NULL); surf = VK_NULL_HANDLE; usleep(50 * 1000); continue; }
            if (ext.width < caps.minImageExtent.width) ext.width = caps.minImageExtent.width;
            if (ext.height < caps.minImageExtent.height) ext.height = caps.minImageExtent.height;
            if (ext.width > caps.maxImageExtent.width) ext.width = caps.maxImageExtent.width;
            if (ext.height > caps.maxImageExtent.height) ext.height = caps.maxImageExtent.height;
            // The M0 probe's finding: the XComponent's native window can still lack its buffer geometry when the surface is handed over, and a window
            // whose geometry does not match the swapchain is one the compositor reports SURFACE_LOST for. Align it with what we are about to present.
            OH_NativeWindow_NativeWindowHandleOpt(win_of(), SET_BUFFER_GEOMETRY, (int32_t)ext.width, (int32_t)ext.height);
            // The Maleoon driver aborted (SIGABRT) inside vkCreateSwapchainKHR with the OPAQUE / transform-from-caps request below, so the
            // values it reports are logged and only what the caps advertise is asked for.
            LOG("[demo] caps: current %ux%u, extent %ux%u..%ux%u, %u..%u images, transform %u (supported 0x%x), alpha 0x%x, usage 0x%x",
                caps.currentExtent.width, caps.currentExtent.height, caps.minImageExtent.width, caps.minImageExtent.height,
                caps.maxImageExtent.width, caps.maxImageExtent.height, caps.minImageCount, caps.maxImageCount,
                caps.currentTransform, caps.supportedTransforms, caps.supportedCompositeAlpha, caps.supportedUsageFlags);
            uint32_t n = caps.minImageCount + 1; if (caps.maxImageCount && n > caps.maxImageCount) n = caps.maxImageCount;
            VkCompositeAlphaFlagBitsKHR alpha = (caps.supportedCompositeAlpha & VK_COMPOSITE_ALPHA_OPAQUE_BIT_KHR)
                ? VK_COMPOSITE_ALPHA_OPAQUE_BIT_KHR : VK_COMPOSITE_ALPHA_INHERIT_BIT_KHR;
            // Identity when the surface allows it: a ROTATE_90 swapchain takes its extent before the rotation, and asking for the screen size
            // there is what aborted the Maleoon driver inside vkCreateSwapchainKHR (SIGABRT, measured).
            VkSurfaceTransformFlagBitsKHR tr = (caps.supportedTransforms & VK_SURFACE_TRANSFORM_IDENTITY_BIT_KHR)
                ? VK_SURFACE_TRANSFORM_IDENTITY_BIT_KHR
                : ((caps.supportedTransforms & caps.currentTransform) ? caps.currentTransform : VK_SURFACE_TRANSFORM_IDENTITY_BIT_KHR);
            if (tr == VK_SURFACE_TRANSFORM_ROTATE_90_BIT_KHR || tr == VK_SURFACE_TRANSFORM_ROTATE_270_BIT_KHR) {
                uint32_t t2 = ext.width; ext.width = ext.height; ext.height = t2;
            }
            VkImageUsageFlags usage = VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT;
            if (caps.supportedUsageFlags & VK_IMAGE_USAGE_TRANSFER_DST_BIT) usage |= VK_IMAGE_USAGE_TRANSFER_DST_BIT;
            LOG("[demo] %u surface formats, using %d/%d, %u images, transform 0x%x, alpha 0x%x, %ux%u", nf, fmt, cs, n, tr, alpha,
                ext.width, ext.height);
            VkSwapchainCreateInfoKHR scc = {VK_STRUCTURE_TYPE_SWAPCHAIN_CREATE_INFO_KHR, NULL, 0, surf, n, fmt, cs, ext, 1,
                                            usage, VK_SHARING_MODE_EXCLUSIVE, 0, NULL, tr,
                                            alpha, VK_PRESENT_MODE_FIFO_KHR, VK_TRUE, VK_NULL_HANDLE};
            if ((r = vkCreateSwapchainKHR(dev, &scc, NULL, &swap)) < 0) { LOG("[demo] vkCreateSwapchainKHR: %d (%ux%u)", r, ext.width, ext.height); swap = VK_NULL_HANDLE; bad++; continue; }
            vkGetSwapchainImagesKHR(dev, swap, &ni, NULL); if (ni > 8) ni = 8; vkGetSwapchainImagesKHR(dev, swap, &ni, imgs);
            VkAttachmentDescription att = {0, fmt, VK_SAMPLE_COUNT_1_BIT, VK_ATTACHMENT_LOAD_OP_CLEAR, VK_ATTACHMENT_STORE_OP_STORE, VK_ATTACHMENT_LOAD_OP_DONT_CARE,
                                           VK_ATTACHMENT_STORE_OP_DONT_CARE, VK_IMAGE_LAYOUT_UNDEFINED, VK_IMAGE_LAYOUT_PRESENT_SRC_KHR};
            VkAttachmentReference ar = {0, VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL};
            VkSubpassDescription sp = {0, VK_PIPELINE_BIND_POINT_GRAPHICS, 0, NULL, 1, &ar, NULL, NULL, 0, NULL};
            VkRenderPassCreateInfo rci = {VK_STRUCTURE_TYPE_RENDER_PASS_CREATE_INFO, NULL, 0, 1, &att, 1, &sp, 0, NULL};
            if (vkCreateRenderPass(dev, &rci, NULL, &rp) < 0) { LOG("[demo] vkCreateRenderPass failed"); return NULL; }
            VkCommandPoolCreateInfo pci = {VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO, NULL, VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT, qf};
            vkCreateCommandPool(dev, &pci, NULL, &pool);
            VkCommandBufferAllocateInfo cbi = {VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO, NULL, pool, VK_COMMAND_BUFFER_LEVEL_PRIMARY, 8};
            vkAllocateCommandBuffers(dev, &cbi, cbs);
            for (uint32_t i = 0; i < ni; i++) {
                VkImageViewCreateInfo vci = {VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO, NULL, 0, imgs[i], VK_IMAGE_VIEW_TYPE_2D, fmt,
                                             {VK_COMPONENT_SWIZZLE_IDENTITY, VK_COMPONENT_SWIZZLE_IDENTITY, VK_COMPONENT_SWIZZLE_IDENTITY, VK_COMPONENT_SWIZZLE_IDENTITY},
                                             {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1}};
                vkCreateImageView(dev, &vci, NULL, &views[i]);
                VkFramebufferCreateInfo fci = {VK_STRUCTURE_TYPE_FRAMEBUFFER_CREATE_INFO, NULL, 0, rp, 1, &views[i], ext.width, ext.height, 1};
                vkCreateFramebuffer(dev, &fci, NULL, &fbs[i]);
            }
            LOG("[demo] window up: %ux%u, format %d, %u images", ext.width, ext.height, fmt, ni);
        }
        uint32_t idx = 0;
        // Bounded: a surface the compositor is not consuming hands out no image at all, and waiting UINT64_MAX there leaves the log silent forever.
        r = vkAcquireNextImageKHR(dev, swap, 100 * 1000 * 1000ULL, VK_NULL_HANDLE, VK_NULL_HANDLE, &idx);
        if (r == VK_TIMEOUT) { if (++waits % 120 == 1) LOG("[demo] no image from the compositor (%d waits, %llu frames)", waits, (unsigned long long)frames); continue; }
        if (r == VK_ERROR_OUT_OF_DATE_KHR) goto rebuild;
        if (r < 0) { LOG("[demo] acquire: %d", r); bad++; usleep(100 * 1000); continue; }
        VkCommandBuffer cb = cbs[idx % 8];
        vkResetCommandBuffer(cb, 0);
        VkCommandBufferBeginInfo bi = {VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO, NULL, VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT};
        vkBeginCommandBuffer(cb, &bi);
        VkClearValue bg = {{{0.02f, 0.03f, 0.06f, 1.0f}}};
        VkRenderPassBeginInfo rbi = {VK_STRUCTURE_TYPE_RENDER_PASS_BEGIN_INFO, NULL, rp, fbs[idx], {{0, 0}, ext}, 1, &bg};
        vkCmdBeginRenderPass(cb, &rbi, VK_SUBPASS_CONTENTS_INLINE);
        {   // four bars marching right, each 1/6 of the height, their colours fixed
            VkClearValue cv[4] = {{{0.06f, 0.86f, 0.47f, 1}}, {{0.30f, 0.55f, 1.00f, 1}}, {{1.00f, 0.72f, 0.20f, 1}}, {{0.95f, 0.30f, 0.55f, 1}}};
            VkClearAttachment ca = {VK_IMAGE_ASPECT_COLOR_BIT, 0, {{0, 0, 0, 0}}};
            float t = (float)frames / 60.0f;
            for (int i = 0; i < 4; i++) {
                int bw = (int)(ext.width / 5), bh = (int)(ext.height / 10);
                int x = (int)((0.5f + 0.5f * sinf(t * (0.6f + 0.15f * i))) * (ext.width - bw));
                int y = (int)(ext.height * (0.12f + 0.2f * i));
                VkClearRect cr = {{{x, y}, {(uint32_t)bw, (uint32_t)bh}}, 0, 1};
                ca.clearValue = cv[i];
                vkCmdClearAttachments(cb, 1, &ca, 1, &cr);
            }
        }
        vkCmdEndRenderPass(cb);
        if (vkEndCommandBuffer(cb) < 0) { LOG("[demo] cannot record"); break; }
        VkSubmitInfo si = {VK_STRUCTURE_TYPE_SUBMIT_INFO, NULL, 0, NULL, NULL, 1, &cb, 0, NULL};
        if (vkQueueSubmit(q, 1, &si, VK_NULL_HANDLE) < 0) { LOG("[demo] submit failed"); bad++; continue; }
        VkPresentInfoKHR pi = {VK_STRUCTURE_TYPE_PRESENT_INFO_KHR, NULL, 0, NULL, 1, &swap, &idx, NULL};
        r = vkQueuePresentKHR(q, &pi);
        vkQueueWaitIdle(q);   // the demo is naive on purpose: one frame in flight keeps the code short
        frames++;
        if (r == VK_ERROR_OUT_OF_DATE_KHR) goto rebuild;
        if (r < 0) { LOG("[demo] present: %d", r); bad++; }
        if (frames % 60 == 0) LOG("[demo] %llu frames presented", (unsigned long long)frames);
        usleep(16000);
        continue;
    rebuild:
        vkDeviceWaitIdle(dev);
        for (uint32_t i = 0; i < ni; i++) { vkDestroyFramebuffer(dev, fbs[i], NULL); vkDestroyImageView(dev, views[i], NULL); }
        if (swap) vkDestroySwapchainKHR(dev, swap, NULL); swap = VK_NULL_HANDLE;
        if (rp) vkDestroyRenderPass(dev, rp, NULL); rp = VK_NULL_HANDLE;
        if (pool) vkDestroyCommandPool(dev, pool, NULL); pool = VK_NULL_HANDLE;
        if (surf) vkDestroySurfaceKHR(inst, surf, NULL); surf = VK_NULL_HANDLE;
    }
    LOG("[demo] stopped after %llu frames", (unsigned long long)frames);
    return NULL;
}

static void *demo_thread(void *arg) {
    void *r = demo_body(arg);
    atomic_store(&g_run, 0);   // whichever way the body left (several error paths return early)
    return r;
}

void dm_ohos_demo_start(void) {
    // The scene is a long-lived thread that rebuilds its swapchain whenever the window changes: a second one would build a second swapchain on
    // the same window (and race the first), so a start while it runs is ignored.
    if (atomic_load(&g_run)) { LOG("[demo] already running"); return; }
    atomic_store(&g_stop, 0);
    atomic_store(&g_run, 1);   // before the thread exists, so a start from another thread cannot slip in
    pthread_t t; pthread_attr_t at; pthread_attr_init(&at); pthread_attr_setstacksize(&at, 1 << 20);
    int rc = pthread_create(&t, &at, demo_thread, NULL);
    if (rc) { atomic_store(&g_run, 0); LOG("[demo] cannot start (%d)", rc); }
    else { pthread_detach(t); LOG("[demo] thread started"); }
    pthread_attr_destroy(&at);
}
void dm_ohos_demo_stop(void) { atomic_store(&g_stop, 1); }
