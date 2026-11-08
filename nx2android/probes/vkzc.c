// vkzc: zero-copy guest memory pools without VK_EXT_external_memory_host.
// nxnative's Metal backend wraps the game's own memory as GPU buffers (newBufferWithBytesNoCopy). Adreno has no host-pointer import, so try the
// opposite direction: allocate GPU memory and place its CPU mapping at a fixed guest address, so the game's pointer *is* GPU memory.
//   remap : vkMapMemory, then mremap(MREMAP_FIXED) the mapping into the guest reservation
//   fd    : export the allocation as an OPAQUE_FD and mmap(MAP_FIXED) that fd at the guest address
//   ahb   : allocate an AHardwareBuffer (BLOB), receive its dma-buf fd through AHardwareBuffer_sendHandleToUnixSocket, mmap(MAP_FIXED) it, import the AHB into Vulkan
// For each: the CPU writes 64 MB at guest address S, the GPU copies S -> D, the CPU checks D; then the CPU rewrites S, the GPU copies again (no flush, as a game would).
// Build: aarch64-linux-android29-clang -O1 vkzc.c -o vkzc -ldl -lnativewindow
#define _GNU_SOURCE
#define VK_NO_PROTOTYPES
#define VK_USE_PLATFORM_ANDROID_KHR
#include <android/hardware_buffer.h>
#include <dlfcn.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>
#include <vulkan/vulkan.h>

#define VKF(n) static PFN_##n n
VKF(vkGetInstanceProcAddr); VKF(vkCreateInstance); VKF(vkEnumeratePhysicalDevices); VKF(vkGetPhysicalDeviceMemoryProperties); VKF(vkGetPhysicalDeviceQueueFamilyProperties);
VKF(vkCreateDevice); VKF(vkGetDeviceProcAddr); VKF(vkGetDeviceQueue); VKF(vkCreateBuffer); VKF(vkAllocateMemory); VKF(vkBindBufferMemory); VKF(vkGetBufferMemoryRequirements);
VKF(vkCreateCommandPool); VKF(vkAllocateCommandBuffers); VKF(vkBeginCommandBuffer); VKF(vkEndCommandBuffer); VKF(vkCmdCopyBuffer); VKF(vkQueueSubmit); VKF(vkQueueWaitIdle);
VKF(vkCmdPipelineBarrier); VKF(vkMapMemory); VKF(vkGetMemoryFdKHR); VKF(vkGetAndroidHardwareBufferPropertiesANDROID); VKF(vkResetCommandBuffer);

static VkDevice dev; static VkQueue q; static VkCommandBuffer cb; static VkPhysicalDeviceMemoryProperties mp;
static const size_t POOL = 64UL << 20;
enum { M_REMAP, M_FD, M_AHB };
static const char *MNAME[] = {"remap", "fd", "ahb"};

static uint32_t pick_type(uint32_t bits, VkMemoryPropertyFlags want) {
    for (uint32_t t = 0; t < mp.memoryTypeCount; t++) if ((bits >> t & 1) && (mp.memoryTypes[t].propertyFlags & want) == want) return t;
    return UINT32_MAX;
}
static VkBuffer new_buf(VkExternalMemoryHandleTypeFlags ht) {
    VkExternalMemoryBufferCreateInfo e = {VK_STRUCTURE_TYPE_EXTERNAL_MEMORY_BUFFER_CREATE_INFO, NULL, ht};
    VkBufferCreateInfo bi = {VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO, ht ? &e : NULL, 0, POOL, VK_BUFFER_USAGE_TRANSFER_SRC_BIT | VK_BUFFER_USAGE_TRANSFER_DST_BIT |
        VK_BUFFER_USAGE_STORAGE_BUFFER_BIT | VK_BUFFER_USAGE_VERTEX_BUFFER_BIT | VK_BUFFER_USAGE_UNIFORM_BUFFER_BIT, VK_SHARING_MODE_EXCLUSIVE};
    VkBuffer b; VkResult r = vkCreateBuffer(dev, &bi, NULL, &b); if (r) { printf("    vkCreateBuffer = %d\n", r); return VK_NULL_HANDLE; }
    return b;
}
static int recv_fds(int sock, int *fds, int max) {   // AHardwareBuffer_sendHandleToUnixSocket sends the native handle's fds as SCM_RIGHTS
    char data[4096], ctl[CMSG_SPACE(sizeof(int) * 16)]; struct iovec iov = {data, sizeof data};
    struct msghdr m = {0}; m.msg_iov = &iov; m.msg_iovlen = 1; m.msg_control = ctl; m.msg_controllen = sizeof ctl;
    if (recvmsg(sock, &m, 0) < 0) return -1;
    int n = 0;
    for (struct cmsghdr *c = CMSG_FIRSTHDR(&m); c; c = CMSG_NXTHDR(&m, c))
        if (c->cmsg_level == SOL_SOCKET && c->cmsg_type == SCM_RIGHTS)
            for (size_t i = 0; i < (c->cmsg_len - CMSG_LEN(0)) / sizeof(int) && n < max; i++) fds[n++] = ((int *)CMSG_DATA(c))[i];
    return n;
}
// A GPU buffer whose CPU view is exactly [ga, ga + POOL)
static VkBuffer make_pool(int method, uint8_t *ga) {
    VkResult r; VkDeviceMemory mem;
    if (method == M_AHB) {
        AHardwareBuffer_Desc d = {(uint32_t)POOL, 1, 1, AHARDWAREBUFFER_FORMAT_BLOB,
            AHARDWAREBUFFER_USAGE_CPU_READ_OFTEN | AHARDWAREBUFFER_USAGE_CPU_WRITE_OFTEN | AHARDWAREBUFFER_USAGE_GPU_DATA_BUFFER, 0, 0, 0};
        AHardwareBuffer *ahb; int e = AHardwareBuffer_allocate(&d, &ahb); if (e) { printf("    AHardwareBuffer_allocate = %d\n", e); return VK_NULL_HANDLE; }
        int sv[2]; socketpair(AF_UNIX, SOCK_SEQPACKET, 0, sv);
        if ((e = AHardwareBuffer_sendHandleToUnixSocket(ahb, sv[0]))) { printf("    sendHandleToUnixSocket = %d\n", e); return VK_NULL_HANDLE; }
        int fds[16]; int n = recv_fds(sv[1], fds, 16);
        printf("    AHB native handle carries %d fd(s)\n", n);
        void *m = MAP_FAILED; int used = -1;
        for (int i = 0; i < n && m == MAP_FAILED; i++) { m = mmap(ga, POOL, PROT_READ | PROT_WRITE, MAP_SHARED | MAP_FIXED, fds[i], 0); if (m != MAP_FAILED) used = i; }
        if (m == MAP_FAILED) { printf("    mmap of the AHB fds at %p failed: %s\n", ga, strerror(errno)); return VK_NULL_HANDLE; }
        printf("    fd %d mapped at %p\n", used, m);
        VkAndroidHardwareBufferPropertiesANDROID ap = {VK_STRUCTURE_TYPE_ANDROID_HARDWARE_BUFFER_PROPERTIES_ANDROID};
        if ((r = vkGetAndroidHardwareBufferPropertiesANDROID(dev, ahb, &ap))) { printf("    AHB properties = %d\n", r); return VK_NULL_HANDLE; }
        VkBuffer b = new_buf(VK_EXTERNAL_MEMORY_HANDLE_TYPE_ANDROID_HARDWARE_BUFFER_BIT_ANDROID); if (!b) return b;
        VkImportAndroidHardwareBufferInfoANDROID imp = {VK_STRUCTURE_TYPE_IMPORT_ANDROID_HARDWARE_BUFFER_INFO_ANDROID, NULL, ahb};
        VkMemoryAllocateInfo ai = {VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO, &imp, ap.allocationSize, pick_type(ap.memoryTypeBits, 0)};
        if ((r = vkAllocateMemory(dev, &ai, NULL, &mem))) { printf("    import AHB = %d\n", r); return VK_NULL_HANDLE; }
        printf("    imported as memory type %u (%#x)\n", ai.memoryTypeIndex, mp.memoryTypes[ai.memoryTypeIndex].propertyFlags);
        vkBindBufferMemory(dev, b, mem, 0); return b;
    }
    VkBuffer b = new_buf(method == M_FD ? VK_EXTERNAL_MEMORY_HANDLE_TYPE_OPAQUE_FD_BIT : 0); if (!b) return b;
    VkMemoryRequirements req; vkGetBufferMemoryRequirements(dev, b, &req);
    uint32_t t = pick_type(req.memoryTypeBits, VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT | VK_MEMORY_PROPERTY_HOST_CACHED_BIT);
    if (t == UINT32_MAX) t = pick_type(req.memoryTypeBits, VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT);
    VkExportMemoryAllocateInfo ex = {VK_STRUCTURE_TYPE_EXPORT_MEMORY_ALLOCATE_INFO, NULL, VK_EXTERNAL_MEMORY_HANDLE_TYPE_OPAQUE_FD_BIT};
    VkMemoryAllocateInfo ai = {VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO, method == M_FD ? &ex : NULL, req.size, t};
    if ((r = vkAllocateMemory(dev, &ai, NULL, &mem))) { printf("    vkAllocateMemory = %d\n", r); return VK_NULL_HANDLE; }
    printf("    memory type %u (%#x)\n", t, mp.memoryTypes[t].propertyFlags);
    vkBindBufferMemory(dev, b, mem, 0);
    if (method == M_REMAP) {
        void *p; if ((r = vkMapMemory(dev, mem, 0, VK_WHOLE_SIZE, 0, &p))) { printf("    vkMapMemory = %d\n", r); return VK_NULL_HANDLE; }
        void *m = mremap(p, req.size, req.size, MREMAP_MAYMOVE | MREMAP_FIXED, ga);
        if (m == MAP_FAILED) { printf("    mremap %p -> %p failed: %s\n", p, ga, strerror(errno)); return VK_NULL_HANDLE; }
        printf("    driver mapping %p moved to %p\n", p, m);
    } else {
        VkMemoryGetFdInfoKHR gi = {VK_STRUCTURE_TYPE_MEMORY_GET_FD_INFO_KHR, NULL, mem, VK_EXTERNAL_MEMORY_HANDLE_TYPE_OPAQUE_FD_BIT};
        int fd; if ((r = vkGetMemoryFdKHR(dev, &gi, &fd))) { printf("    vkGetMemoryFdKHR = %d\n", r); return VK_NULL_HANDLE; }
        void *m = mmap(ga, req.size, PROT_READ | PROT_WRITE, MAP_SHARED | MAP_FIXED, fd, 0);
        if (m == MAP_FAILED) { printf("    mmap(opaque fd %d) at %p failed: %s\n", fd, ga, strerror(errno)); return VK_NULL_HANDLE; }
        printf("    opaque fd %d mapped at %p\n", fd, m);
    }
    return b;
}
static double now_ms(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec * 1e3 + t.tv_nsec / 1e6; }
static size_t gpu_copy_check(VkBuffer s, VkBuffer d, uint32_t *S, uint32_t *D, uint32_t seed, double *ms) {
    for (size_t i = 0; i < POOL / 4; i++) S[i] = (uint32_t)(i * 2654435761u) ^ seed;   // plain CPU stores, no flush: what a game does
    memset(D, 0, POOL);
    vkResetCommandBuffer(cb, 0);
    VkCommandBufferBeginInfo bbi = {VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO}; vkBeginCommandBuffer(cb, &bbi);
    VkBufferCopy reg = {0, 0, POOL}; vkCmdCopyBuffer(cb, s, d, 1, &reg);
    VkMemoryBarrier mb = {VK_STRUCTURE_TYPE_MEMORY_BARRIER, NULL, VK_ACCESS_TRANSFER_WRITE_BIT, VK_ACCESS_HOST_READ_BIT};
    vkCmdPipelineBarrier(cb, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_HOST_BIT, 0, 1, &mb, 0, NULL, 0, NULL);
    vkEndCommandBuffer(cb);
    VkSubmitInfo si = {VK_STRUCTURE_TYPE_SUBMIT_INFO, NULL, 0, NULL, NULL, 1, &cb, 0, NULL};
    double t0 = now_ms(); vkQueueSubmit(q, 1, &si, VK_NULL_HANDLE); vkQueueWaitIdle(q); *ms = now_ms() - t0;
    size_t bad = 0; for (size_t i = 0; i < POOL / 4; i++) bad += D[i] != ((uint32_t)(i * 2654435761u) ^ seed);
    return bad;
}

int main(int argc, char **argv) {
    void *lib = dlopen("libvulkan.so", RTLD_NOW);
    vkGetInstanceProcAddr = (PFN_vkGetInstanceProcAddr)dlsym(lib, "vkGetInstanceProcAddr");
    VkInstance inst = VK_NULL_HANDLE;
#define IGET(n) n = (PFN_##n)vkGetInstanceProcAddr(inst, #n)
    IGET(vkCreateInstance);
    VkApplicationInfo app = {VK_STRUCTURE_TYPE_APPLICATION_INFO, NULL, "vkzc", 1, NULL, 0, VK_API_VERSION_1_3};
    VkInstanceCreateInfo ici = {VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, NULL, 0, &app};
    if (vkCreateInstance(&ici, NULL, &inst)) return 1;
    IGET(vkEnumeratePhysicalDevices); IGET(vkGetPhysicalDeviceMemoryProperties); IGET(vkGetPhysicalDeviceQueueFamilyProperties); IGET(vkCreateDevice); IGET(vkGetDeviceProcAddr);
    uint32_t n = 1; VkPhysicalDevice pd; vkEnumeratePhysicalDevices(inst, &n, &pd); vkGetPhysicalDeviceMemoryProperties(pd, &mp);
    uint32_t qn = 16; VkQueueFamilyProperties qp[16]; vkGetPhysicalDeviceQueueFamilyProperties(pd, &qn, qp);
    uint32_t qf = 0; while (qf < qn && !(qp[qf].queueFlags & VK_QUEUE_GRAPHICS_BIT)) qf++;
    float prio = 1; VkDeviceQueueCreateInfo qci = {VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO, NULL, 0, qf, 1, &prio};
    const char *exts[] = {"VK_KHR_external_memory_fd", "VK_ANDROID_external_memory_android_hardware_buffer", "VK_EXT_queue_family_foreign"};
    VkDeviceCreateInfo dci = {VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO, NULL, 0, 1, &qci, 0, NULL, 3, exts};
    VkResult r = vkCreateDevice(pd, &dci, NULL, &dev); if (r) { printf("vkCreateDevice = %d\n", r); return 1; }
#define DGET(n) n = (PFN_##n)vkGetDeviceProcAddr(dev, #n)
    DGET(vkGetDeviceQueue); DGET(vkCreateBuffer); DGET(vkAllocateMemory); DGET(vkBindBufferMemory); DGET(vkGetBufferMemoryRequirements); DGET(vkCreateCommandPool);
    DGET(vkAllocateCommandBuffers); DGET(vkBeginCommandBuffer); DGET(vkEndCommandBuffer); DGET(vkCmdCopyBuffer); DGET(vkQueueSubmit); DGET(vkQueueWaitIdle);
    DGET(vkCmdPipelineBarrier); DGET(vkMapMemory); DGET(vkGetMemoryFdKHR); DGET(vkGetAndroidHardwareBufferPropertiesANDROID); DGET(vkResetCommandBuffer);
    vkGetDeviceQueue(dev, qf, 0, &q);
    VkCommandPoolCreateInfo pci = {VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO, NULL, VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT, qf}; VkCommandPool pool; vkCreateCommandPool(dev, &pci, NULL, &pool);
    VkCommandBufferAllocateInfo cai = {VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO, NULL, pool, VK_COMMAND_BUFFER_LEVEL_PRIMARY, 1}; vkAllocateCommandBuffers(dev, &cai, &cb);

    // the guest address space: one 8 GB PROT_NONE reservation, as nxnative's host makes
    uint8_t *as = mmap(NULL, 8UL << 30, PROT_NONE, MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE, -1, 0);
    printf("guest reservation %p + 8 GB\n", as);
    {   // baseline: ordinary anonymous memory, same loops, to tell whether a pool mapping is CPU-cached
        uint64_t *a = mmap(NULL, POOL, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0); memset(a, 1, POOL);
        double c0 = now_ms(); volatile uint64_t sum = 0; for (size_t i = 0; i < POOL / 8; i++) sum += a[i]; double cr = now_ms() - c0;
        c0 = now_ms(); for (size_t i = 0; i < POOL / 8; i++) ((volatile uint64_t *)a)[i] = i; double cw = now_ms() - c0;
        printf("baseline anon memory: read 64 MB %.1f ms, write %.1f ms\n", cr, cw);
    }
    for (int m = 0; m < 3; m++) {
        if (argc > 1 && strcmp(argv[1], MNAME[m])) continue;
        uint8_t *S = as + (1UL << 30) * (m + 1), *D = S + POOL + 0x10000;   // 4 KB-aligned, not size-aligned, like a game's heap allocation
        printf("== %s (guest pools at %p and %p)\n", MNAME[m], S, D);
        VkBuffer bs = make_pool(m, S), bd = bs ? make_pool(m, D) : VK_NULL_HANDLE;
        if (!bd) { printf("RESULT %s: FAILED to set up\n", MNAME[m]); continue; }
        double t1, t2; size_t bad1 = gpu_copy_check(bs, bd, (uint32_t *)S, (uint32_t *)D, 0, &t1), bad2 = gpu_copy_check(bs, bd, (uint32_t *)S, (uint32_t *)D, 0xa5a5a5a5, &t2);
        double c0 = now_ms(); volatile uint64_t sum = 0; for (size_t i = 0; i < POOL / 8; i++) sum += ((uint64_t *)S)[i]; double cr = now_ms() - c0;
        c0 = now_ms(); for (size_t i = 0; i < POOL / 8; i++) ((volatile uint64_t *)S)[i] = i; double cw = now_ms() - c0;
        printf("    pass 1: %zu wrong words, GPU copy %.2f ms; pass 2 (rewrite, no flush): %zu wrong; pool mapping: read 64 MB %.1f ms, write %.1f ms\n", bad1, t1, bad2, cr, cw);
        printf("RESULT %s: %s\n", MNAME[m], bad1 || bad2 ? "data mismatch" : "WORKS - the guest pointer is GPU memory, zero copy");
    }
    return 0;
}
