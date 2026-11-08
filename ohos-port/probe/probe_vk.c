/* probe_vk.c — M0.2 Vulkan/VK_OHOS_surface 上屏 + M0.3 BCn/ASTC 格式探测
 *
 * 链路（进程内执行，不 fork —— Vulkan surface 与进程图形连接绑定，
 * fork 后建 swapchain 会 SURFACE_LOST）：
 *   实例扩展枚举 → VK_OHOS_surface? → 实例(api≥1.1) → 等待 XComponent 窗口 →
 *   GetProcAddress("vkCreateSurfaceOHOS") → surface → 设备/队列 → swapchain →
 *   纯色 #0FDC78 帧上屏（首帧计时 present_ms）→ 连续 300 帧落 probe_frames.jsonl
 *   （TC0-2-3，collect_frames.py 消费）→ BCn×16 + ASTC 4x4/8x8 探测。
 *
 * 判定契约（B 文档）：
 *   - vk.has_vk_ohos_surface = "VK_OHOS_surface" 扩展存在（伪代码语义；
 *     后续阶段失败写入 note，不翻转该位）；
 *   - bcn[i] = VkFormatProperties2.optimalTilingFeatures & SAMPLED_IMAGE_BIT；
 *   - present 计时：CLOCK_MONOTONIC 包裹 vkQueueSubmit+fence 等待+vkQueuePresentKHR，
 *     present mode 固定 FIFO；仅记录不判定（FIFO@60Hz 会阻塞至 vsync）。
 */
#include "probe_checks.h"
#include "probe_report.h"
#include "vk_ohos_shim.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <native_window/external_window.h>
#include <vulkan/vulkan.h>

/* ---------- 窗口注入（XComponent OnSurfaceCreated，见 probe_main.c） ---------- */
static void *volatile g_window;   /* OHNativeWindow*：单次赋值 + 指针宽读，探针够用 */

void probe_vk_set_window(void *window)
{
    g_window = window;
}

/* 等待 XComponent 窗口注入（OnSurfaceCreated 发生在 Ability 首帧后，探针 worker
 * 起得比 UI 快）。父进程 fork 前先调一次消除竞态；子进程内可再调兜底。 */
bool probe_vk_wait_window(int seconds)
{
    for (int i = 0; i < seconds * 20; i++) {
        if (g_window)
            return true;
        struct timespec ms50 = { .tv_sec = 0, .tv_nsec = 50 * 1000 * 1000 };
        nanosleep(&ms50, NULL);
    }
    return false;
}

/* ---------- frames.jsonl（TC0-2-3 时间戳落盘） ---------- */
static const char *FRAMES_PATH_PRIMARY  = "/data/local/tmp/probe_frames.jsonl";
static const char *FRAMES_PATH_FALLBACK = "/data/storage/el2/base/files/probe_frames.jsonl";

static FILE *frames_open(void)
{
    FILE *f = fopen(FRAMES_PATH_PRIMARY, "wb");
    if (!f)
        f = fopen(FRAMES_PATH_FALLBACK, "wb");
    return f;
}

static uint64_t mono_ns(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ULL + (uint64_t)ts.tv_nsec;
}

/* ---------- 工具 ---------- */
#define VKC(x)                                                         \
    do {                                                               \
        VkResult _rc = (x);                                            \
        if (_rc != VK_SUCCESS) {                                       \
            snprintf(note, sizeof note, "%s rc=%d at line %d", #x,     \
                     (int)_rc, __LINE__);                              \
            goto fail;                                                 \
        }                                                              \
    } while (0)

static bool has_ext(const VkExtensionProperties *ext, uint32_t n, const char *name)
{
    for (uint32_t i = 0; i < n; i++)
        if (strcmp(ext[i].extensionName, name) == 0)
            return true;
    return false;
}

/* ---------- M0.3 BCn / ASTC ---------- */
/* 探测全集：游戏可能用到的所有 BC 格式 + sRGB 变体（缺一个都可能触发转码需求）。
 * 表内容与 probe_json.c 的 probe_bcn_keys[] 同源（bcn_formats.def），
 * 顺序不一致在结构上不可能发生（旧的逐项 _Static_assert 对数组元素非法）。 */
static const VkFormat BCN_FORMATS[] = {
#define BCN_ENTRY(fmt) fmt,
#include "bcn_formats.def"
#undef BCN_ENTRY
};
_Static_assert(sizeof(BCN_FORMATS) / sizeof(BCN_FORMATS[0]) == PROBE_BCn_COUNT,
               "bcn_formats.def 项数必须等于 PROBE_BCn_COUNT");

static bool probe_bcn(VkPhysicalDevice pd, ProbeReport *r, char *note, size_t notecap)
{
    int missing = 0;
    for (int i = 0; i < PROBE_BCn_COUNT; i++) {
        VkFormatProperties2 p2 = { .sType = VK_STRUCTURE_TYPE_FORMAT_PROPERTIES_2 };
        vkGetPhysicalDeviceFormatProperties2(pd, BCN_FORMATS[i], &p2);
        r->bcn[i] = (p2.formatProperties.optimalTilingFeatures &
                     VK_FORMAT_FEATURE_SAMPLED_IMAGE_BIT) != 0;
        if (!r->bcn[i])
            missing++;
    }
    r->bcn_all = probe_bcn_all(r->bcn);

    /* ASTC：转码兜底（M3.4）的落点 */
    VkFormatProperties2 a4 = { .sType = VK_STRUCTURE_TYPE_FORMAT_PROPERTIES_2 };
    VkFormatProperties2 a8 = { .sType = VK_STRUCTURE_TYPE_FORMAT_PROPERTIES_2 };
    vkGetPhysicalDeviceFormatProperties2(pd, VK_FORMAT_ASTC_4x4_UNORM_BLOCK, &a4);
    vkGetPhysicalDeviceFormatProperties2(pd, VK_FORMAT_ASTC_8x8_UNORM_BLOCK, &a8);
    r->astc_4x4 = (a4.formatProperties.optimalTilingFeatures &
                   VK_FORMAT_FEATURE_SAMPLED_IMAGE_BIT) != 0;
    r->astc_8x8 = (a8.formatProperties.optimalTilingFeatures &
                   VK_FORMAT_FEATURE_SAMPLED_IMAGE_BIT) != 0;

    snprintf(note, notecap, "missing=%d astc=%d/%d", missing, (int)r->astc_4x4,
             (int)r->astc_8x8);
    return true;   /* 探测本身不失败：缺格式是"结论"不是"探针错误" */
}

/* ---------- G0.2 readback 兜底：设备侧 RGBA8 转储 ----------
 * DM_DUMP_FRAMEBUFFER=<path> 时导出最后一帧：swapchain image →
 * CopyImageToBuffer → host visible → （BGRA 时 swizzle）→ 写 .bin。
 * PC 侧 verify_pixel.py --mode readback 消费（截屏色域偏移时的判定路径）。 */
static uint32_t find_mem_type(VkPhysicalDevice pd, uint32_t type_bits,
                              VkMemoryPropertyFlags want)
{
    VkPhysicalDeviceMemoryProperties mp;
    vkGetPhysicalDeviceMemoryProperties(pd, &mp);
    for (uint32_t i = 0; i < mp.memoryTypeCount; i++)
        if ((type_bits & (1u << i)) &&
            (mp.memoryTypes[i].propertyFlags & want) == want)
            return i;
    return UINT32_MAX;
}

static bool fmt_is_bgra(VkFormat f)
{
    return f == VK_FORMAT_B8G8R8A8_UNORM || f == VK_FORMAT_B8G8R8A8_SRGB;
}

static bool dump_frame_rgba(VkPhysicalDevice pd, VkDevice device, VkQueue queue,
                            VkCommandPool pool, VkImage img, uint32_t w,
                            uint32_t h, bool swap_rb, const char *path,
                            char *note, size_t notecap)
{
    VkBufferCreateInfo bci = { .sType  = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO,
                               .size   = (VkDeviceSize)w * h * 4,
                               .usage  = VK_BUFFER_USAGE_TRANSFER_DST_BIT,
                               .sharingMode = VK_SHARING_MODE_EXCLUSIVE };
    VkBuffer         buf   = VK_NULL_HANDLE;
    VkDeviceMemory   mem   = VK_NULL_HANDLE;
    VkCommandBuffer  cmd2  = VK_NULL_HANDLE;
    VkFence          fence2 = VK_NULL_HANDLE;
    void *           mapped = NULL;
    bool             ok    = false;
    if (vkCreateBuffer(device, &bci, NULL, &buf) != VK_SUCCESS) {
        snprintf(note, notecap, "dump: buffer create failed");
        return false;
    }
    VkMemoryRequirements mr;
    vkGetBufferMemoryRequirements(device, buf, &mr);
    uint32_t mti = find_mem_type(pd, mr.memoryTypeBits,
                                 VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT |
                                     VK_MEMORY_PROPERTY_HOST_COHERENT_BIT);
    if (mti == UINT32_MAX) {
        snprintf(note, notecap, "dump: no host visible mem type");
        goto out;
    }
    if (vkAllocateMemory(device, &(VkMemoryAllocateInfo){
                                     .sType     = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO,
                                     .allocationSize = mr.size,
                                     .memoryTypeIndex = mti },
                                 NULL, &mem) != VK_SUCCESS ||
        vkBindBufferMemory(device, buf, mem, 0) != VK_SUCCESS) {
        snprintf(note, notecap, "dump: alloc/bind failed");
        goto out;
    }
    if (vkAllocateCommandBuffers(device, &(VkCommandBufferAllocateInfo){
            .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO,
            .commandPool = pool, .level = VK_COMMAND_BUFFER_LEVEL_PRIMARY,
            .commandBufferCount = 1 }, &cmd2) != VK_SUCCESS ||
        vkCreateFence(device, &(VkFenceCreateInfo){
                          .sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO },
                      NULL, &fence2) != VK_SUCCESS ||
        vkBeginCommandBuffer(cmd2, &(VkCommandBufferBeginInfo){
            .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO,
            .flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT }) != VK_SUCCESS) {
        snprintf(note, notecap, "dump: cmd setup failed");
        goto out;
    }
    {
        /* WaitIdle 后所有 swapchain image 停在 PRESENT_SRC（present 消费完） */
        VkImageSubresourceRange range = { .aspectMask = VK_IMAGE_ASPECT_COLOR_BIT,
                                          .levelCount = 1,
                                          .layerCount = 1 };
        VkImageMemoryBarrier b1 = {
            .sType         = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER,
            .srcAccessMask = 0,
            .dstAccessMask = VK_ACCESS_TRANSFER_READ_BIT,
            .oldLayout     = VK_IMAGE_LAYOUT_PRESENT_SRC_KHR,
            .newLayout     = VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL,
            .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
            .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
            .image         = img,
            .subresourceRange    = range,
        };
        vkCmdPipelineBarrier(cmd2, VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT,
                             VK_PIPELINE_STAGE_TRANSFER_BIT, 0, 0, NULL, 0, NULL,
                             1, &b1);
        VkBufferImageCopy cpy = {
            .imageSubresource = { .aspectMask = VK_IMAGE_ASPECT_COLOR_BIT,
                                  .layerCount = 1 },
            .imageExtent      = { w, h, 1 },
        };
        vkCmdCopyImageToBuffer(cmd2, img, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL,
                               buf, 1, &cpy);
        VkImageMemoryBarrier b2 = b1;
        b2.srcAccessMask = VK_ACCESS_TRANSFER_READ_BIT;
        b2.oldLayout     = VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL;
        b2.newLayout     = VK_IMAGE_LAYOUT_PRESENT_SRC_KHR;
        vkCmdPipelineBarrier(cmd2, VK_PIPELINE_STAGE_TRANSFER_BIT,
                             VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT, 0, 0, NULL, 0,
                             NULL, 1, &b2);
    }
    if (vkEndCommandBuffer(cmd2) != VK_SUCCESS ||
        vkQueueSubmit(queue, 1, &(VkSubmitInfo){
            .sType = VK_STRUCTURE_TYPE_SUBMIT_INFO,
            .commandBufferCount = 1, .pCommandBuffers = &cmd2 }, fence2) != VK_SUCCESS ||
        vkWaitForFences(device, 1, &fence2, VK_TRUE,
                        3000ULL * 1000 * 1000) != VK_SUCCESS) {
        snprintf(note, notecap, "dump: submit/wait failed");
        goto out;
    }
    if (vkMapMemory(device, mem, 0, bci.size, 0, &mapped) != VK_SUCCESS) {
        snprintf(note, notecap, "dump: map failed");
        goto out;
    }
    {
        FILE *f = fopen(path, "wb");
        if (!f) {
            snprintf(note, notecap, "dump: open %s failed", path);
            goto out;
        }
        /* surface 为 B8G8R8A8 时 buffer 字节序是 BGRA；readback 期望 RGBA8 */
        const unsigned char *src = (const unsigned char *)mapped;
        size_t               npx = (size_t)w * h;
        for (size_t i = 0; i < npx; i++) {
            unsigned char        px[4];
            const unsigned char *s4 = src + i * 4;
            if (swap_rb) {
                px[0] = s4[2];
                px[1] = s4[1];
                px[2] = s4[0];
            } else {
                px[0] = s4[0];
                px[1] = s4[1];
                px[2] = s4[2];
            }
            px[3] = s4[3];
            if (fwrite(px, 1, 4, f) != 4) {
                snprintf(note, notecap, "dump: write failed");
                fclose(f);
                goto out;
            }
        }
        ok = fclose(f) == 0;
        if (ok)
            snprintf(note, notecap, "dump: %s (%ux%u)", path, w, h);
    }

out:
    if (mapped)
        vkUnmapMemory(device, mem);
    if (fence2)
        vkDestroyFence(device, fence2, NULL);
    /* cmd2 不单独释放：pool 带 RESET bit，随 pool 销毁回收 */
    if (mem)
        vkFreeMemory(device, mem, NULL);
    if (buf)
        vkDestroyBuffer(device, buf, NULL);
    return ok;
}

/* ---------- 主链路 ---------- */
bool probe_vk_run(ProbeReport *r)
{
    char note[128] = "";
    VkInstance       instance = VK_NULL_HANDLE;
    VkSurfaceKHR     surface  = VK_NULL_HANDLE;
    VkPhysicalDevice pdev     = VK_NULL_HANDLE;
    VkDevice         device   = VK_NULL_HANDLE;
    VkSwapchainKHR   swap     = VK_NULL_HANDLE;
    VkCommandPool    pool     = VK_NULL_HANDLE;
    VkCommandBuffer  cmd      = VK_NULL_HANDLE;
    VkFence          fence    = VK_NULL_HANDLE;
    VkSemaphore      sem_acq  = VK_NULL_HANDLE;   /* acquire → submit 等待 */
    VkSemaphore      sem_rdy  = VK_NULL_HANDLE;   /* submit signal → present 等待 */
    VkQueue          queue    = VK_NULL_HANDLE;
    bool             bcn_done = false;
    FILE *           frames   = NULL;
    /* goto fail 会跳过中途声明 → 统一在顶部置 NULL，fail 路径统一释放 */
    VkExtensionProperties *exts = NULL;
    VkPhysicalDevice *      pds = NULL;
    VkQueueFamilyProperties *qfs = NULL;
    VkSurfaceFormatKHR *    fmts = NULL;
    VkImage *               imgs = NULL;

    /* 1) 实例扩展可用性（n==0 或枚举失败均记 false，不用 VLA） */
    uint32_t n = 0;
    if (vkEnumerateInstanceExtensionProperties(NULL, &n, NULL) != VK_SUCCESS || n == 0) {
        snprintf(note, sizeof note, "instance ext enum failed/empty");
        goto fail;
    }
    exts = (VkExtensionProperties *)malloc(n * sizeof *exts);
    if (!exts) {
        snprintf(note, sizeof note, "oom ext list");
        goto fail;
    }
    if (vkEnumerateInstanceExtensionProperties(NULL, &n, exts) != VK_SUCCESS) {
        snprintf(note, sizeof note, "instance ext enum failed");
        goto fail;
    }
    bool has_ohos = has_ext(exts, n, "VK_OHOS_surface");
    bool has_khr  = has_ext(exts, n, "VK_KHR_surface");
    free(exts);
    exts = NULL;   /* 提前释放后置空，防 fail 路径二次 free */
    r->vk_surface.ok = has_ohos;            /* 契约位：扩展存在性（伪代码语义） */
    if (!has_khr) {
        /* 后续 surface 支持查询/swapchain 全是 KHR 入口，缺了链路无法继续 */
        snprintf(note, sizeof note, "VK_KHR_surface missing (required)");
        goto fail;
    }
    if (!has_ohos) {
        snprintf(note, sizeof note, "VK_OHOS_surface missing");
        goto fail;
    }

    /* 2) 实例（请求 1.1；实际 api_version 由物理设备属性回填） */
    const char *inst_exts[2] = { "VK_KHR_surface", "VK_OHOS_surface" };
    uint32_t    inst_ext_n   = 2u;
    VkApplicationInfo app = { .sType              = VK_STRUCTURE_TYPE_APPLICATION_INFO,
                              .pApplicationName   = "dm-probe",
                              .applicationVersion = VK_MAKE_VERSION(0, 1, 0),
                              .apiVersion         = VK_API_VERSION_1_1 };
    VkInstanceCreateInfo ici = { .sType                   = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO,
                                 .pApplicationInfo        = &app,
                                 .enabledExtensionCount   = inst_ext_n,
                                 .ppEnabledExtensionNames = inst_exts };
    VKC(vkCreateInstance(&ici, NULL, &instance));

    /* 3) XComponent 窗口（探针 UI 全屏 XComponent 注入，10s 超时兜底；
     *    正常时父进程在 fork 前已等到窗口，这里通常立即返回） */
    if (!probe_vk_wait_window(10)) {
        snprintf(note, sizeof note, "no XComponent window in 10s");
        goto fail;
    }
    /* 窗口刚注入时还没 attach 到合成器，等 300ms 再建 surface */
    {
        struct timespec ms300 = { .tv_sec = 0, .tv_nsec = 300 * 1000 * 1000 };
        nanosleep(&ms300, NULL);
    }

    /* 4) surface —— 真 SDK 签名：直接吃 VkSurfaceCreateInfoOHOS（无 KHR pNext 链） */
    PFN_vkCreateSurfaceOHOS pfn = (PFN_vkCreateSurfaceOHOS)vkGetInstanceProcAddr(
        instance, "vkCreateSurfaceOHOS");
    if (!pfn) {
        snprintf(note, sizeof note, "vkCreateSurfaceOHOS not found");
        goto fail;
    }
    VkSurfaceCreateInfoOHOS sci = {
        .sType  = VK_STRUCTURE_TYPE_SURFACE_CREATE_INFO_OHOS,
        .pNext  = NULL,          /* 规范要求 NULL */
        .flags  = 0,             /* 规范要求 0 */
        .window = (OHNativeWindow *)g_window,
    };
    VKC(pfn(instance, &sci, NULL, &surface));

    /* 5) 物理设备（优先独显）+ 属性/驱动留档（G0.2 附加记录） */
    uint32_t npd = 0;
    VKC(vkEnumeratePhysicalDevices(instance, &npd, NULL));
    if (npd == 0) {
        snprintf(note, sizeof note, "no physical device");
        goto fail;
    }
    pds = (VkPhysicalDevice *)malloc(npd * sizeof *pds);
    if (!pds) {
        snprintf(note, sizeof note, "oom pds");
        goto fail;
    }
    VKC(vkEnumeratePhysicalDevices(instance, &npd, pds));
    pdev = pds[0];
    for (uint32_t i = 1; i < npd; i++) {
        VkPhysicalDeviceProperties tmp;
        vkGetPhysicalDeviceProperties(pds[i], &tmp);
        VkPhysicalDeviceProperties cur;
        vkGetPhysicalDeviceProperties(pdev, &cur);
        if (tmp.deviceType == VK_PHYSICAL_DEVICE_TYPE_DISCRETE_GPU &&
            cur.deviceType != VK_PHYSICAL_DEVICE_TYPE_DISCRETE_GPU)
            pdev = pds[i];
    }
    VkPhysicalDeviceProperties props;
    vkGetPhysicalDeviceProperties(pdev, &props);
    r->vk_api_version = props.apiVersion;
    snprintf(r->vk_device_name, sizeof r->vk_device_name, "%s", props.deviceName);
    snprintf(r->vk_driver_info, sizeof r->vk_driver_info, "vendor=0x%x dev=0x%x ver=0x%x",
             props.vendorID, props.deviceID, props.driverVersion);

    /* 6) 队列族（GRAPHICS + present 支持） */
    uint32_t nqf = 0;
    vkGetPhysicalDeviceQueueFamilyProperties(pdev, &nqf, NULL);
    qfs = (VkQueueFamilyProperties *)malloc(nqf * sizeof *qfs);
    if (!qfs) {
        snprintf(note, sizeof note, "oom qfs");
        goto fail;
    }
    vkGetPhysicalDeviceQueueFamilyProperties(pdev, &nqf, qfs);
    int qfam = -1;
    for (uint32_t i = 0; i < nqf; i++) {
        if (!(qfs[i].queueFlags & VK_QUEUE_GRAPHICS_BIT))
            continue;
        VkBool32 sup = VK_FALSE;
        if (vkGetPhysicalDeviceSurfaceSupportKHR(pdev, i, surface, &sup) == VK_SUCCESS &&
            sup) {
            qfam = (int)i;
            break;
        }
    }
    if (qfam < 0) {
        snprintf(note, sizeof note, "no graphics+present queue family");
        goto fail;
    }

    /* 7) 设备（swapchain） */
    const char *dev_exts[1] = { "VK_KHR_swapchain" };
    float       prio        = 1.0f;
    VkDeviceQueueCreateInfo qci = { .sType            = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO,
                                      .queueFamilyIndex = (uint32_t)qfam,
                                      .queueCount       = 1,
                                      .pQueuePriorities = &prio };
    VkDeviceCreateInfo dci = { .sType                   = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO,
                               .queueCreateInfoCount    = 1,
                               .pQueueCreateInfos       = &qci,
                               .enabledExtensionCount   = 1,
                               .ppEnabledExtensionNames = dev_exts };
    VKC(vkCreateDevice(pdev, &dci, NULL, &device));
    vkGetDeviceQueue(device, (uint32_t)qfam, 0, &queue);

    /* 8) 交换链（FIFO；usage 含 TRANSFER_DST 清屏 + TRANSFER_SRC 供 readback dump） */
    VkSurfaceCapabilitiesKHR caps;
    VKC(vkGetPhysicalDeviceSurfaceCapabilitiesKHR(pdev, surface, &caps));
    if (caps.currentExtent.width == 0xFFFFFFFFu ||
        caps.currentExtent.height == 0xFFFFFFFFu ||
        caps.currentExtent.width == 0 || caps.currentExtent.height == 0) {
        /* 0xFFFFFFFF = 由用户设定（XComponent 未完成 layout 时会出现）；
         * 0 = 窗口尚未成形。均无法直接建 swapchain。 */
        snprintf(note, sizeof note, "currentExtent %ux%u not ready",
                 caps.currentExtent.width, caps.currentExtent.height);
        goto fail;
    }
    bool transfer_src_ok =
        (caps.supportedUsageFlags & VK_IMAGE_USAGE_TRANSFER_SRC_BIT) != 0;
    uint32_t fmt_n = 0;
    VKC(vkGetPhysicalDeviceSurfaceFormatsKHR(pdev, surface, &fmt_n, NULL));
    if (fmt_n == 0) {
        snprintf(note, sizeof note, "no surface formats");
        goto fail;
    }
    fmts = (VkSurfaceFormatKHR *)malloc(fmt_n * sizeof *fmts);
    if (!fmts) {
        snprintf(note, sizeof note, "oom fmts");
        goto fail;
    }
    VKC(vkGetPhysicalDeviceSurfaceFormatsKHR(pdev, surface, &fmt_n, fmts));
    VkSurfaceFormatKHR fmt = fmts[0];
    for (uint32_t i = 0; i < fmt_n; i++) {   /* 优先 8bit UNORM 便于像素验证 */
        if ((fmts[i].format == VK_FORMAT_R8G8B8A8_UNORM ||
             fmts[i].format == VK_FORMAT_B8G8R8A8_UNORM) &&
            fmts[i].colorSpace == VK_COLOR_SPACE_SRGB_NONLINEAR_KHR) {
            fmt = fmts[i];
            break;
        }
    }

    uint32_t img_count = caps.minImageCount + 1;
    if (caps.maxImageCount > 0 && img_count > caps.maxImageCount)
        img_count = caps.maxImageCount;

    /* XComponent 的 OHNativeWindow 在 surfaceId 注入瞬间可能还没定尺寸，
     * 先把 buffer geometry 对齐到 caps 给的 extent 再说（真机上不做这一步
     * 会直接 SURFACE_LOST）。 */
    if (g_window)
        OH_NativeWindow_NativeWindowHandleOpt((OHNativeWindow *)g_window,
                                              SET_BUFFER_GEOMETRY,
                                              (int32_t)caps.currentExtent.width,
                                              (int32_t)caps.currentExtent.height);

    /* compositeAlpha 必须取自 caps.supportedCompositeAlpha，OPAQUE 不在其中
     * 时挑第一个支持的位 */
    VkCompositeAlphaFlagBitsKHR alpha = VK_COMPOSITE_ALPHA_OPAQUE_BIT_KHR;
    if (!(caps.supportedCompositeAlpha & VK_COMPOSITE_ALPHA_OPAQUE_BIT_KHR)) {
        static const VkCompositeAlphaFlagBitsKHR order[] = {
            VK_COMPOSITE_ALPHA_INHERIT_BIT_KHR,
            VK_COMPOSITE_ALPHA_PRE_MULTIPLIED_BIT_KHR,
            VK_COMPOSITE_ALPHA_POST_MULTIPLIED_BIT_KHR,
        };
        alpha = VK_COMPOSITE_ALPHA_INHERIT_BIT_KHR;
        for (size_t i = 0; i < sizeof order / sizeof order[0]; i++) {
            if (caps.supportedCompositeAlpha & order[i]) {
                alpha = order[i];
                break;
            }
        }
    }

    VkImageUsageFlags usage = VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT |
                              VK_IMAGE_USAGE_TRANSFER_DST_BIT |
                              (transfer_src_ok ? VK_IMAGE_USAGE_TRANSFER_SRC_BIT : 0);
    usage &= caps.supportedUsageFlags;
    if (!(usage & VK_IMAGE_USAGE_TRANSFER_DST_BIT)) {
        snprintf(note, sizeof note, "no TRANSFER_DST usage (supported=0x%x)",
                 caps.supportedUsageFlags);
        goto fail;
    }

    VkSwapchainCreateInfoKHR sci2 = {
        .sType                 = VK_STRUCTURE_TYPE_SWAPCHAIN_CREATE_INFO_KHR,
        .surface               = surface,
        .minImageCount         = img_count,
        .imageFormat           = fmt.format,
        .imageColorSpace       = fmt.colorSpace,
        .imageExtent           = caps.currentExtent,
        .imageArrayLayers      = 1,
        .imageUsage            = usage,
        .imageSharingMode      = VK_SHARING_MODE_EXCLUSIVE,
        .preTransform          = caps.currentTransform,
        .compositeAlpha        = alpha,
        .presentMode           = VK_PRESENT_MODE_FIFO_KHR,   /* 契约：固定 FIFO */
        .clipped               = VK_TRUE,
    };
    /* 建 surface 到建 swapchain 之间窗口可能还在 attach，重试三次；
     * 每次失败都把 rc 留档，便于区分 SURFACE_LOST / OUT_OF_DATE */
    VkResult sw_rc = VK_SUCCESS;
    for (int attempt = 0; attempt < 3; attempt++) {
        sw_rc = vkCreateSwapchainKHR(device, &sci2, NULL, &swap);
        if (sw_rc == VK_SUCCESS)
            break;
        struct timespec ms200 = { .tv_sec = 0, .tv_nsec = 200 * 1000 * 1000 };
        nanosleep(&ms200, NULL);
    }
    if (sw_rc != VK_SUCCESS) {
        snprintf(note, sizeof note,
                 "swapchain rc=%d ext=%ux%u alpha=0x%x usage=0x%x n=%u",
                 (int)sw_rc, caps.currentExtent.width, caps.currentExtent.height,
                 (unsigned)alpha, (unsigned)usage, img_count);
        goto fail;
    }

    VKC(vkCreateCommandPool(device, &(VkCommandPoolCreateInfo){
            .sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO,
            .flags = VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT,
            .queueFamilyIndex = (uint32_t)qfam }, NULL, &pool));
    VKC(vkAllocateCommandBuffers(device, &(VkCommandBufferAllocateInfo){
            .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO,
            .commandPool = pool, .level = VK_COMMAND_BUFFER_LEVEL_PRIMARY,
            .commandBufferCount = 1 }, &cmd));
    VKC(vkCreateFence(device, &(VkFenceCreateInfo){
            .sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO }, NULL, &fence));
    VKC(vkCreateSemaphore(device, &(VkSemaphoreCreateInfo){
            .sType = VK_STRUCTURE_TYPE_SEMAPHORE_CREATE_INFO }, NULL, &sem_acq));
    VKC(vkCreateSemaphore(device, &(VkSemaphoreCreateInfo){
            .sType = VK_STRUCTURE_TYPE_SEMAPHORE_CREATE_INFO }, NULL, &sem_rdy));

    frames = frames_open();   /* 可为 NULL：frames 缺失只影响 TC0-2-3 留档 */

    /* 9) 300 帧：acquire → 清屏 #0FDC78 → submit(计时含 fence 等待) → present(计时) */
    uint32_t nim = 0;
    VKC(vkGetSwapchainImagesKHR(device, swap, &nim, NULL));
    imgs = (VkImage *)malloc((nim ? nim : 1) * sizeof *imgs);
    if (!imgs) {
        snprintf(note, sizeof note, "oom imgs");
        goto fail;
    }
    VKC(vkGetSwapchainImagesKHR(device, swap, &nim, imgs));

    const VkClearColorValue clear = { .float32 = { 15.0f / 255.0f, 220.0f / 255.0f,
                                                   120.0f / 255.0f, 1.0f } };
    VkImageSubresourceRange range = { .aspectMask = VK_IMAGE_ASPECT_COLOR_BIT,
                                      .levelCount = 1,
                                      .layerCount = 1 };
    uint32_t done = 0;
    for (uint32_t i = 0; i < 300; i++) {
        uint32_t idx = 0;
        VkResult arc = vkAcquireNextImageKHR(device, swap, 500ULL * 1000 * 1000,
                                             sem_acq, VK_NULL_HANDLE, &idx);
        if (arc != VK_SUCCESS && arc != VK_SUBOPTIMAL_KHR) {
            snprintf(note, sizeof note, "acquire rc=%d at frame %u", (int)arc, i);
            goto fail;
        }
        VkCommandBufferBeginInfo bi = { .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO,
                                        .flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT };
        VKC(vkBeginCommandBuffer(cmd, &bi));
        /* oldLayout 固定 UNDEFINED：整帧随后被 clear，不依赖跨帧 layout 跟踪
         * （swapchain image 复用时驱动可自行丢弃旧内容） */
        VkImageMemoryBarrier bar = {
            .sType               = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER,
            .srcAccessMask       = 0,
            .dstAccessMask       = VK_ACCESS_TRANSFER_WRITE_BIT,
            .oldLayout           = VK_IMAGE_LAYOUT_UNDEFINED,
            .newLayout           = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,
            .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
            .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
            .image               = imgs[idx],
            .subresourceRange    = range,
        };
        vkCmdPipelineBarrier(cmd, VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT,
                             VK_PIPELINE_STAGE_TRANSFER_BIT, 0, 0, NULL, 0, NULL, 1, &bar);
        vkCmdClearColorImage(cmd, imgs[idx], VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,
                             &clear, 1, &range);
        VkImageMemoryBarrier bar2 = bar;
        bar2.srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;
        bar2.dstAccessMask = 0;
        bar2.oldLayout     = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL;
        bar2.newLayout     = VK_IMAGE_LAYOUT_PRESENT_SRC_KHR;
        vkCmdPipelineBarrier(cmd, VK_PIPELINE_STAGE_TRANSFER_BIT,
                             VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT, 0, 0, NULL, 0, NULL,
                             1, &bar2);
        VKC(vkEndCommandBuffer(cmd));

        /* 信号量配对：acquire(sem_acq) → submit 等 sem_acq / 唤 sem_rdy →
         * present 等 sem_rdy（规范要求 present 前的 image 依赖经信号量传递） */
        VkPipelineStageFlags wait_stage = VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT;
        VkSubmitInfo si = { .sType                = VK_STRUCTURE_TYPE_SUBMIT_INFO,
                            .waitSemaphoreCount   = 1,
                            .pWaitSemaphores      = &sem_acq,
                            .pWaitDstStageMask    = &wait_stage,
                            .commandBufferCount   = 1,
                            .pCommandBuffers      = &cmd,
                            .signalSemaphoreCount = 1,
                            .pSignalSemaphores    = &sem_rdy };
        uint64_t t0 = mono_ns();   /* 计时口径：submit + fence 等待 + present */
        VKC(vkQueueSubmit(queue, 1, &si, fence));
        VKC(vkWaitForFences(device, 1, &fence, VK_TRUE, 1000ULL * 1000 * 1000));
        VkPresentInfoKHR pi = { .sType            = VK_STRUCTURE_TYPE_PRESENT_INFO_KHR,
                                .waitSemaphoreCount = 1,
                                .pWaitSemaphores    = &sem_rdy,
                                .swapchainCount     = 1,
                                .pSwapchains        = &swap,
                                .pImageIndices      = &idx };
        VkResult prc = vkQueuePresentKHR(queue, &pi);
        uint64_t t1 = mono_ns();
        VKC(vkResetFences(device, 1, &fence));
        if (prc != VK_SUCCESS && prc != VK_SUBOPTIMAL_KHR) {
            snprintf(note, sizeof note, "present rc=%d at frame %u", (int)prc, i);
            goto fail;
        }
        if (i == 0)
            r->present_ms = (double)(t1 - t0) / 1e6;
        if (frames)
            fprintf(frames, "{\"i\":%u,\"submit_ns\":%llu,\"present_ns\":%llu,"
                            "\"ms\":%.3f}\n",
                    i, (unsigned long long)t0, (unsigned long long)t1,
                    (double)(t1 - t0) / 1e6);
        done = i + 1;
    }
    if (frames) {
        fclose(frames);
        frames = NULL;
    }
    vkDeviceWaitIdle(device);

    /* G0.2 readback 兜底：DM_DUMP_FRAMEBUFFER=<path> 时导出一个已 present
     * 的槽 imgs[0]（内容为最后一次写入该槽的帧；300 帧多槽复用，不保证是
     * 全局最后一帧——对纯色探针帧无差别）（只记录，
     * 失败不翻转主结论——截屏路径仍可用） */
    const char *dump_path = getenv("DM_DUMP_FRAMEBUFFER");
    if (dump_path && dump_path[0] && done > 0) {
        char dnote[64] = "";
        bool dok = false;
        if (!transfer_src_ok)
            snprintf(dnote, sizeof dnote, "dump: TRANSFER_SRC unsupported");
        else
            dok = dump_frame_rgba(pdev, device, queue, pool, imgs[0],
                                  caps.currentExtent.width,
                                  caps.currentExtent.height,
                                  fmt_is_bgra(fmt.format), dump_path, dnote,
                                  sizeof dnote);
        size_t nl = strlen(r->vk_surface.note);
        snprintf(r->vk_surface.note + nl, sizeof r->vk_surface.note - nl,
                 " dump=%d(%s)", (int)dok, dnote);
    }

    /* 10) M0.3 BCn/ASTC（同子进程，物理设备句柄仍有效） */
    bcn_done = probe_bcn(pdev, r, note, sizeof note);

    {
        size_t nl   = strlen(r->vk_surface.note);
        snprintf(r->vk_surface.note + nl, sizeof r->vk_surface.note - nl,
                 " frames=%u present=%.3fms", done, r->present_ms);
    }
    free(pds);
    free(qfs);
    free(fmts);
    free(imgs);
    if (swap) vkDestroySwapchainKHR(device, swap, NULL);
    if (pool) vkDestroyCommandPool(device, pool, NULL);
    if (fence) vkDestroyFence(device, fence, NULL);
    if (sem_acq) vkDestroySemaphore(device, sem_acq, NULL);
    if (sem_rdy) vkDestroySemaphore(device, sem_rdy, NULL);
    if (device) vkDestroyDevice(device, NULL);
    if (surface) vkDestroySurfaceKHR(instance, surface, NULL);
    if (instance) vkDestroyInstance(instance, NULL);
    (void)bcn_done;
    return r->vk_surface.ok && done == 300 && r->bcn_all;

fail:
    if (frames) fclose(frames);
    free(exts);
    free(pds);
    free(qfs);
    free(fmts);
    free(imgs);
    /* fail 可能发生在队列尚有在途提交时（如 present 失败）——先等空闲再销毁 */
    if (device) vkDeviceWaitIdle(device);
    if (pool) vkDestroyCommandPool(device, pool, NULL);
    if (fence) vkDestroyFence(device, fence, NULL);
    if (sem_acq) vkDestroySemaphore(device, sem_acq, NULL);
    if (sem_rdy) vkDestroySemaphore(device, sem_rdy, NULL);
    if (swap) vkDestroySwapchainKHR(device, swap, NULL);
    if (device) vkDestroyDevice(device, NULL);
    if (surface) vkDestroySurfaceKHR(instance, surface, NULL);
    if (instance) vkDestroyInstance(instance, NULL);
    snprintf(r->vk_surface.note, sizeof r->vk_surface.note, "%s", note);
    return false;
}
