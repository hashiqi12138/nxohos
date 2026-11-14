// vkprobe: the Vulkan facts an NVN -> Vulkan backend for nxnative depends on, measured on the device.
// 1. Identity, features, limits and extensions that matter for Switch titles.
// 2. BC1-BC7 / ASTC format support (Switch packaged textures are mostly BCn and ASTC).
// 3. A real zero-copy test: import ordinary mmap'ed memory with VK_EXT_external_memory_host, write it from the CPU, copy it on the
//    GPU into a second imported buffer and check the CPU sees the result. nxnative's Metal backend relies on this (newBufferWithBytesNoCopy).
// Build: aarch64-linux-android29-clang -O1 vkprobe.c -o vkprobe -lvulkan
#define _GNU_SOURCE
#define VK_NO_PROTOTYPES
#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
#include <vulkan/vulkan.h>

#define VKF(n) static PFN_##n n
VKF(vkGetInstanceProcAddr); VKF(vkCreateInstance); VKF(vkEnumeratePhysicalDevices); VKF(vkGetPhysicalDeviceProperties2); VKF(vkGetPhysicalDeviceFeatures2);
VKF(vkEnumerateDeviceExtensionProperties); VKF(vkGetPhysicalDeviceFormatProperties); VKF(vkGetPhysicalDeviceMemoryProperties); VKF(vkGetPhysicalDeviceQueueFamilyProperties);
VKF(vkCreateDevice); VKF(vkGetDeviceProcAddr); VKF(vkEnumerateInstanceVersion);
VKF(vkGetDeviceQueue); VKF(vkCreateBuffer); VKF(vkAllocateMemory); VKF(vkBindBufferMemory); VKF(vkGetBufferMemoryRequirements); VKF(vkCreateCommandPool);
VKF(vkAllocateCommandBuffers); VKF(vkBeginCommandBuffer); VKF(vkEndCommandBuffer); VKF(vkCmdCopyBuffer); VKF(vkQueueSubmit); VKF(vkQueueWaitIdle);
VKF(vkCmdPipelineBarrier); VKF(vkGetBufferDeviceAddress);
static PFN_vkGetMemoryHostPointerPropertiesEXT pGetHostPtrProps;

static int has_ext(VkExtensionProperties *e, uint32_t n, const char *name) { for (uint32_t i = 0; i < n; i++) if (!strcmp(e[i].extensionName, name)) return 1; return 0; }
#define YN(x) ((x) ? "yes" : "NO ")

static int zero_copy_test(VkPhysicalDevice pd, uint32_t qf, int bda, VkDeviceSize align, uint32_t *out_types);

int main(void) {
    void *lib = dlopen("libvulkan.so", RTLD_NOW);
    if (!lib) { printf("no libvulkan.so: %s\n", dlerror()); return 1; }
    vkGetInstanceProcAddr = (PFN_vkGetInstanceProcAddr)dlsym(lib, "vkGetInstanceProcAddr");
#define IGET(n) n = (PFN_##n)vkGetInstanceProcAddr(inst, #n)
    VkInstance inst = VK_NULL_HANDLE;
    IGET(vkCreateInstance); IGET(vkEnumerateInstanceVersion);
    uint32_t iver = VK_API_VERSION_1_0; if (vkEnumerateInstanceVersion) vkEnumerateInstanceVersion(&iver);
    VkApplicationInfo app = {VK_STRUCTURE_TYPE_APPLICATION_INFO, NULL, "vkprobe", 1, NULL, 0, iver >= VK_API_VERSION_1_3 ? VK_API_VERSION_1_3 : iver};
    VkInstanceCreateInfo ici = {VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, NULL, 0, &app, 0, NULL, 0, NULL};
    VkResult r = vkCreateInstance(&ici, NULL, &inst);
    if (r) { printf("vkCreateInstance failed: %d\n", r); return 1; }
    IGET(vkEnumeratePhysicalDevices); IGET(vkGetPhysicalDeviceProperties2); IGET(vkGetPhysicalDeviceFeatures2); IGET(vkEnumerateDeviceExtensionProperties);
    IGET(vkGetPhysicalDeviceFormatProperties); IGET(vkGetPhysicalDeviceMemoryProperties); IGET(vkGetPhysicalDeviceQueueFamilyProperties); IGET(vkCreateDevice); IGET(vkGetDeviceProcAddr);
    uint32_t npd = 1; VkPhysicalDevice pd; vkEnumeratePhysicalDevices(inst, &npd, &pd);
    if (!npd) { printf("no physical device\n"); return 1; }

    // ---- identity and limits
    VkPhysicalDeviceExternalMemoryHostPropertiesEXT hostp = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_EXTERNAL_MEMORY_HOST_PROPERTIES_EXT};
    VkPhysicalDeviceDriverProperties drv = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_DRIVER_PROPERTIES, &hostp};
    VkPhysicalDeviceVulkan11Properties p11 = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_1_PROPERTIES, &drv};
    VkPhysicalDeviceProperties2 props = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2, &p11};
    vkGetPhysicalDeviceProperties2(pd, &props);
    VkPhysicalDeviceProperties *P = &props.properties; VkPhysicalDeviceLimits *L = &P->limits;
    printf("== device\n%s  vendor %#x device %#x  API %u.%u.%u  driver %s %s\n", P->deviceName, P->vendorID, P->deviceID,
           VK_API_VERSION_MAJOR(P->apiVersion), VK_API_VERSION_MINOR(P->apiVersion), VK_API_VERSION_PATCH(P->apiVersion), drv.driverName, drv.driverInfo);
    printf("subgroup %u  maxStorageBufferRange %u MB  maxMemoryAllocationCount %u  maxPerStageSampledImages %u  maxBoundDescriptorSets %u\n",
           p11.subgroupSize, L->maxStorageBufferRange >> 20, L->maxMemoryAllocationCount, L->maxPerStageDescriptorSampledImages, L->maxBoundDescriptorSets);
    printf("maxComputeSharedMemory %u  maxComputeWGInvocations %u  minStorageBufferOffsetAlign %llu  minUniformBufferOffsetAlign %llu  maxPushConstants %u  maxVertexInputBindings %u  maxColorAttachments %u\n",
           L->maxComputeSharedMemorySize, L->maxComputeWorkGroupInvocations, (unsigned long long)L->minStorageBufferOffsetAlignment,
           (unsigned long long)L->minUniformBufferOffsetAlignment, L->maxPushConstantsSize, L->maxVertexInputBindings, L->maxColorAttachments);

    // ---- features
    VkPhysicalDeviceVulkan13Features f13 = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_3_FEATURES};
    VkPhysicalDeviceVulkan12Features f12 = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES, P->apiVersion >= VK_API_VERSION_1_3 ? &f13 : NULL};
    VkPhysicalDeviceFeatures2 feats = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2, &f12};
    vkGetPhysicalDeviceFeatures2(pd, &feats);
    VkPhysicalDeviceFeatures *F = &feats.features;
    printf("== features\n");
    printf("textureCompressionBC %s  ETC2 %s  ASTC_LDR %s  geometryShader %s  tessellation %s  shaderInt64 %s  shaderFloat64 %s\n",
           YN(F->textureCompressionBC), YN(F->textureCompressionETC2), YN(F->textureCompressionASTC_LDR), YN(F->geometryShader), YN(F->tessellationShader), YN(F->shaderInt64), YN(F->shaderFloat64));
    printf("multiDrawIndirect %s  drawIndirectFirstInstance %s  depthClamp %s  depthBiasClamp %s  logicOp %s  dualSrcBlend %s  independentBlend %s  fillModeNonSolid %s  wideLines %s\n",
           YN(F->multiDrawIndirect), YN(F->drawIndirectFirstInstance), YN(F->depthClamp), YN(F->depthBiasClamp), YN(F->logicOp), YN(F->dualSrcBlend), YN(F->independentBlend), YN(F->fillModeNonSolid), YN(F->wideLines));
    printf("imageCubeArray %s  storageImageReadWithoutFormat %s  WriteWithoutFormat %s  vertexStoresAndAtomics %s  fragmentStoresAndAtomics %s  clipDistance %s  cullDistance %s  samplerAnisotropy %s\n",
           YN(F->imageCubeArray), YN(F->shaderStorageImageReadWithoutFormat), YN(F->shaderStorageImageWriteWithoutFormat), YN(F->vertexPipelineStoresAndAtomics),
           YN(F->fragmentStoresAndAtomics), YN(F->shaderClipDistance), YN(F->shaderCullDistance), YN(F->samplerAnisotropy));
    printf("1.2: descriptorIndexing %s  runtimeDescriptorArray %s  sampledImageNonUniform %s  partiallyBound %s  updateAfterBindSampledImage %s  bufferDeviceAddress %s  BDA captureReplay %s\n",
           YN(f12.descriptorIndexing), YN(f12.runtimeDescriptorArray), YN(f12.shaderSampledImageArrayNonUniformIndexing), YN(f12.descriptorBindingPartiallyBound),
           YN(f12.descriptorBindingSampledImageUpdateAfterBind), YN(f12.bufferDeviceAddress), YN(f12.bufferDeviceAddressCaptureReplay));
    printf("1.2: timelineSemaphore %s  scalarBlockLayout %s  shaderFloat16 %s  shaderInt8 %s  storageBuffer8Bit %s  drawIndirectCount %s  samplerFilterMinmax %s\n",
           YN(f12.timelineSemaphore), YN(f12.scalarBlockLayout), YN(f12.shaderFloat16), YN(f12.shaderInt8), YN(f12.storageBuffer8BitAccess), YN(f12.drawIndirectCount), YN(f12.samplerFilterMinmax));
    if (P->apiVersion >= VK_API_VERSION_1_3)
        printf("1.3: dynamicRendering %s  synchronization2 %s  maintenance4 %s  inlineUniformBlock %s\n", YN(f13.dynamicRendering), YN(f13.synchronization2), YN(f13.maintenance4), YN(f13.inlineUniformBlock));

    // ---- extensions
    uint32_t ne = 0; vkEnumerateDeviceExtensionProperties(pd, NULL, &ne, NULL);
    VkExtensionProperties *ex = calloc(ne, sizeof *ex); vkEnumerateDeviceExtensionProperties(pd, NULL, &ne, ex);
    static const char *WANT[] = {"VK_EXT_external_memory_host", "VK_EXT_descriptor_buffer", "VK_EXT_descriptor_indexing", "VK_KHR_push_descriptor", "VK_EXT_transform_feedback",
        "VK_EXT_conditional_rendering", "VK_EXT_extended_dynamic_state", "VK_EXT_extended_dynamic_state2", "VK_EXT_extended_dynamic_state3", "VK_EXT_vertex_input_dynamic_state",
        "VK_EXT_custom_border_color", "VK_EXT_border_color_swizzle", "VK_EXT_provoking_vertex", "VK_EXT_depth_clip_control", "VK_EXT_depth_clip_enable",
        "VK_EXT_shader_stencil_export", "VK_EXT_shader_viewport_index_layer", "VK_EXT_line_rasterization", "VK_EXT_index_type_uint8", "VK_EXT_vertex_attribute_divisor",
        "VK_EXT_robustness2", "VK_EXT_image_robustness", "VK_EXT_primitive_topology_list_restart", "VK_EXT_memory_budget", "VK_EXT_pipeline_creation_cache_control",
        "VK_KHR_pipeline_library", "VK_EXT_graphics_pipeline_library", "VK_EXT_shader_object", "VK_KHR_shader_float_controls", "VK_KHR_maintenance5",
        "VK_EXT_attachment_feedback_loop_layout", "VK_EXT_rasterization_order_attachment_access", "VK_ARM_rasterization_order_attachment_access",
        "VK_QCOM_render_pass_shader_resolve", "VK_EXT_map_memory_placed", "VK_KHR_map_memory2", "VK_ANDROID_external_memory_android_hardware_buffer",
        "VK_EXT_external_memory_dma_buf", "VK_KHR_external_memory_fd", "VK_EXT_texture_compression_astc_hdr", "VK_EXT_image_compression_control",
        "VK_KHR_shader_subgroup_extended_types", "VK_EXT_subgroup_size_control", "VK_KHR_fragment_shading_rate", "VK_EXT_mesh_shader", "VK_EXT_multi_draw"};
    printf("== extensions (%u total)\n", ne);
    for (unsigned i = 0; i < sizeof WANT / sizeof *WANT; i++) printf("%s %s\n", YN(has_ext(ex, ne, WANT[i])), WANT[i]);

    // ---- texture formats (optimal tiling, sampled)
    static const struct { VkFormat f; const char *n; } FMT[] = {
        {VK_FORMAT_BC1_RGBA_UNORM_BLOCK, "BC1"}, {VK_FORMAT_BC2_UNORM_BLOCK, "BC2"}, {VK_FORMAT_BC3_UNORM_BLOCK, "BC3"}, {VK_FORMAT_BC4_UNORM_BLOCK, "BC4"},
        {VK_FORMAT_BC5_UNORM_BLOCK, "BC5"}, {VK_FORMAT_BC6H_UFLOAT_BLOCK, "BC6H"}, {VK_FORMAT_BC7_UNORM_BLOCK, "BC7"}, {VK_FORMAT_BC7_SRGB_BLOCK, "BC7_sRGB"},
        {VK_FORMAT_ASTC_4x4_UNORM_BLOCK, "ASTC4x4"}, {VK_FORMAT_ASTC_8x8_SRGB_BLOCK, "ASTC8x8s"}, {VK_FORMAT_ETC2_R8G8B8A8_UNORM_BLOCK, "ETC2"},
        {VK_FORMAT_D24_UNORM_S8_UINT, "D24S8"}, {VK_FORMAT_D32_SFLOAT_S8_UINT, "D32S8"}, {VK_FORMAT_A2B10G10R10_UNORM_PACK32, "RGB10A2"},
        {VK_FORMAT_B10G11R11_UFLOAT_PACK32, "R11G11B10F"}, {VK_FORMAT_E5B9G9R9_UFLOAT_PACK32, "RGB9E5"}, {VK_FORMAT_R16G16B16A16_UNORM, "RGBA16UN"},
        {VK_FORMAT_R16G16B16A16_SNORM, "RGBA16SN"}, {VK_FORMAT_R32G32B32_SFLOAT, "RGB32F(vtx)"}};
    printf("== formats (S = sampled, C = color attachment, D = depth, B = blit src, F = linear filter)\n");
    for (unsigned i = 0; i < sizeof FMT / sizeof *FMT; i++) {
        VkFormatProperties fp; vkGetPhysicalDeviceFormatProperties(pd, FMT[i].f, &fp); VkFormatFeatureFlags o = fp.optimalTilingFeatures;
        printf("%-11s %c%c%c%c%c%s\n", FMT[i].n, o & VK_FORMAT_FEATURE_SAMPLED_IMAGE_BIT ? 'S' : '-', o & VK_FORMAT_FEATURE_COLOR_ATTACHMENT_BIT ? 'C' : '-',
               o & VK_FORMAT_FEATURE_DEPTH_STENCIL_ATTACHMENT_BIT ? 'D' : '-', o & VK_FORMAT_FEATURE_BLIT_SRC_BIT ? 'B' : '-',
               o & VK_FORMAT_FEATURE_SAMPLED_IMAGE_FILTER_LINEAR_BIT ? 'F' : '-', fp.bufferFeatures & VK_FORMAT_FEATURE_VERTEX_BUFFER_BIT ? "  (vertex buffer ok)" : "");
    }

    // ---- memory
    VkPhysicalDeviceMemoryProperties mp; vkGetPhysicalDeviceMemoryProperties(pd, &mp);
    printf("== memory\n");
    for (uint32_t i = 0; i < mp.memoryHeapCount; i++) printf("heap %u: %llu MB%s\n", i, (unsigned long long)(mp.memoryHeaps[i].size >> 20), mp.memoryHeaps[i].flags & VK_MEMORY_HEAP_DEVICE_LOCAL_BIT ? " device-local" : "");
    for (uint32_t i = 0; i < mp.memoryTypeCount; i++) {
        VkMemoryPropertyFlags f = mp.memoryTypes[i].propertyFlags;
        printf("type %u (heap %u):%s%s%s%s%s\n", i, mp.memoryTypes[i].heapIndex, f & 1 ? " DEVICE_LOCAL" : "", f & 2 ? " HOST_VISIBLE" : "", f & 4 ? " HOST_COHERENT" : "", f & 8 ? " HOST_CACHED" : "", f & 16 ? " LAZY" : "");
    }

    // ---- zero-copy import test
    printf("== zero-copy (VK_EXT_external_memory_host)\n");
    if (!has_ext(ex, ne, "VK_EXT_external_memory_host")) { printf("not supported: an NVN backend must shadow guest memory pools (copy + dirty tracking)\n"); return 0; }
    printf("minImportedHostPointerAlignment = %llu\n", (unsigned long long)hostp.minImportedHostPointerAlignment);
    uint32_t qn = 0; vkGetPhysicalDeviceQueueFamilyProperties(pd, &qn, NULL); VkQueueFamilyProperties qp[16]; if (qn > 16) qn = 16; vkGetPhysicalDeviceQueueFamilyProperties(pd, &qn, qp);
    uint32_t qf = 0; while (qf < qn && !(qp[qf].queueFlags & VK_QUEUE_GRAPHICS_BIT)) qf++;
    uint32_t types = 0;
    int ok = zero_copy_test(pd, qf, f12.bufferDeviceAddress, hostp.minImportedHostPointerAlignment, &types);
    printf("RESULT: %s\n", ok ? "zero-copy import WORKS (guest memory pools can be GPU buffers directly)" : "zero-copy import FAILED");
    return 0;
}

static VkDevice dev;
static int import_buf(void *ptr, VkDeviceSize size, int bda, VkBuffer *buf, VkDeviceMemory *mem, uint32_t *types) {
    VkMemoryHostPointerPropertiesEXT hp = {VK_STRUCTURE_TYPE_MEMORY_HOST_POINTER_PROPERTIES_EXT};
    VkResult r = pGetHostPtrProps(dev, VK_EXTERNAL_MEMORY_HANDLE_TYPE_HOST_ALLOCATION_BIT_EXT, ptr, &hp);
    if (r) { printf("  vkGetMemoryHostPointerPropertiesEXT(%p) = %d\n", ptr, r); return 0; }
    *types = hp.memoryTypeBits;
    VkExternalMemoryBufferCreateInfo ebi = {VK_STRUCTURE_TYPE_EXTERNAL_MEMORY_BUFFER_CREATE_INFO, NULL, VK_EXTERNAL_MEMORY_HANDLE_TYPE_HOST_ALLOCATION_BIT_EXT};
    VkBufferCreateInfo bi = {VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO, &ebi, 0, size,
        VK_BUFFER_USAGE_TRANSFER_SRC_BIT | VK_BUFFER_USAGE_TRANSFER_DST_BIT | VK_BUFFER_USAGE_STORAGE_BUFFER_BIT | VK_BUFFER_USAGE_VERTEX_BUFFER_BIT |
        VK_BUFFER_USAGE_INDEX_BUFFER_BIT | VK_BUFFER_USAGE_UNIFORM_BUFFER_BIT | (bda ? VK_BUFFER_USAGE_SHADER_DEVICE_ADDRESS_BIT : 0), VK_SHARING_MODE_EXCLUSIVE};
    if ((r = vkCreateBuffer(dev, &bi, NULL, buf))) { printf("  vkCreateBuffer = %d\n", r); return 0; }
    VkMemoryRequirements req; vkGetBufferMemoryRequirements(dev, *buf, &req);
    uint32_t t = 0; uint32_t bits = hp.memoryTypeBits & req.memoryTypeBits; while (t < 32 && !(bits >> t & 1)) t++;
    if (t == 32) { printf("  no memory type for the import (host bits %#x, buffer bits %#x)\n", hp.memoryTypeBits, req.memoryTypeBits); return 0; }
    VkMemoryAllocateFlagsInfo fl = {VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_FLAGS_INFO, NULL, bda ? VK_MEMORY_ALLOCATE_DEVICE_ADDRESS_BIT : 0};
    VkImportMemoryHostPointerInfoEXT imp = {VK_STRUCTURE_TYPE_IMPORT_MEMORY_HOST_POINTER_INFO_EXT, &fl, VK_EXTERNAL_MEMORY_HANDLE_TYPE_HOST_ALLOCATION_BIT_EXT, ptr};
    VkMemoryAllocateInfo ai = {VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO, &imp, size, t};
    if ((r = vkAllocateMemory(dev, &ai, NULL, mem))) { printf("  vkAllocateMemory(import %p, %llu B, type %u) = %d\n", ptr, (unsigned long long)size, t, r); return 0; }
    if ((r = vkBindBufferMemory(dev, *buf, *mem, 0))) { printf("  vkBindBufferMemory = %d\n", r); return 0; }
    if (bda) {
        VkBufferDeviceAddressInfo dai = {VK_STRUCTURE_TYPE_BUFFER_DEVICE_ADDRESS_INFO, NULL, *buf};
        VkDeviceAddress ga = vkGetBufferDeviceAddress(dev, &dai);
        printf("  imported %p (%llu MB, type %u): GPU address %#llx%s\n", ptr, (unsigned long long)(size >> 20), t, (unsigned long long)ga, ga == (uintptr_t)ptr ? " == CPU address" : " (differs from CPU address)");
    } else printf("  imported %p (%llu MB, type %u)\n", ptr, (unsigned long long)(size >> 20), t);
    return 1;
}
static int zero_copy_test(VkPhysicalDevice pd, uint32_t qf, int bda, VkDeviceSize align, uint32_t *types) {
    float prio = 1;
    VkDeviceQueueCreateInfo qci = {VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO, NULL, 0, qf, 1, &prio};
    const char *exts[] = {"VK_EXT_external_memory_host"};
    VkPhysicalDeviceVulkan12Features en12 = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES}; en12.bufferDeviceAddress = bda;
    VkDeviceCreateInfo dci = {VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO, &en12, 0, 1, &qci, 0, NULL, 1, exts, NULL};
    VkResult r = vkCreateDevice(pd, &dci, NULL, &dev);
    if (r) { printf("  vkCreateDevice = %d\n", r); return 0; }
#define DGET(n) n = (PFN_##n)vkGetDeviceProcAddr(dev, #n)
    DGET(vkGetDeviceQueue); DGET(vkCreateBuffer); DGET(vkAllocateMemory); DGET(vkBindBufferMemory); DGET(vkGetBufferMemoryRequirements); DGET(vkCreateCommandPool);
    DGET(vkAllocateCommandBuffers); DGET(vkBeginCommandBuffer); DGET(vkEndCommandBuffer); DGET(vkCmdCopyBuffer); DGET(vkQueueSubmit); DGET(vkQueueWaitIdle);
    DGET(vkCmdPipelineBarrier); DGET(vkGetBufferDeviceAddress);
    pGetHostPtrProps = (PFN_vkGetMemoryHostPointerPropertiesEXT)vkGetDeviceProcAddr(dev, "vkGetMemoryHostPointerPropertiesEXT");
    // the guest-like layout: one big PROT_NONE reservation (nxnative's guest address space), a 64 MB "memory pool" made RW inside it at a 4 KB-aligned offset
    const size_t RES = 8UL << 30, POOL = 64UL << 20;
    uint8_t *as = mmap(NULL, RES, PROT_NONE, MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE, -1, 0);
    uint8_t *src = as + (1UL << 30) + 0x1000 * (align <= 0x1000), *dst = src + POOL + 0x10000;
    if (as == MAP_FAILED || mprotect(as + (1UL << 30), 2 * POOL + 0x20000, PROT_READ | PROT_WRITE)) { printf("  reservation failed\n"); return 0; }
    printf("  pools inside an 8 GB reservation at %p: src %p dst %p\n", as, src, dst);
    VkBuffer bs, bd; VkDeviceMemory ms, md;
    if (!import_buf(src, POOL, bda, &bs, &ms, types) || !import_buf(dst, POOL, bda, &bd, &md, types)) return 0;
    for (size_t i = 0; i < POOL / 4; i++) ((uint32_t *)src)[i] = (uint32_t)(i * 2654435761u);   // the CPU writes the source pool in place
    memset(dst, 0, POOL);
    VkQueue q; vkGetDeviceQueue(dev, qf, 0, &q);
    VkCommandPoolCreateInfo pci = {VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO, NULL, 0, qf}; VkCommandPool pool; vkCreateCommandPool(dev, &pci, NULL, &pool);
    VkCommandBufferAllocateInfo cai = {VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO, NULL, pool, VK_COMMAND_BUFFER_LEVEL_PRIMARY, 1}; VkCommandBuffer cb; vkAllocateCommandBuffers(dev, &cai, &cb);
    VkCommandBufferBeginInfo bbi = {VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO}; vkBeginCommandBuffer(cb, &bbi);
    VkBufferCopy reg = {0, 0, POOL}; vkCmdCopyBuffer(cb, bs, bd, 1, &reg);
    VkMemoryBarrier mb = {VK_STRUCTURE_TYPE_MEMORY_BARRIER, NULL, VK_ACCESS_TRANSFER_WRITE_BIT, VK_ACCESS_HOST_READ_BIT};
    vkCmdPipelineBarrier(cb, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_HOST_BIT, 0, 1, &mb, 0, NULL, 0, NULL);
    vkEndCommandBuffer(cb);
    VkSubmitInfo si = {VK_STRUCTURE_TYPE_SUBMIT_INFO, NULL, 0, NULL, NULL, 1, &cb, 0, NULL};
    if ((r = vkQueueSubmit(q, 1, &si, VK_NULL_HANDLE))) { printf("  submit = %d\n", r); return 0; }
    vkQueueWaitIdle(q);
    size_t badw = 0; for (size_t i = 0; i < POOL / 4; i++) badw += ((uint32_t *)dst)[i] != (uint32_t)(i * 2654435761u);
    printf("  GPU copied 64 MB between the imported pools; CPU sees %zu wrong words of %zu\n", badw, POOL / 4);
    return badw == 0;
}
