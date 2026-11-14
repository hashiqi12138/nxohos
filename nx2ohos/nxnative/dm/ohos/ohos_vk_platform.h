/* HarmonyOS platform shims for the Vulkan backend shared with the Android port (dm/android/gfx_vk_*.c).
 *
 * What is platform specific in that backend is only (a) where the window handle comes from, (b) the surface call,
 * and (c) the "NVN pool memory is GPU memory" shortcut, which is an AHardwareBuffer import on Android. On HarmonyOS:
 *   (a) OHNativeWindow, owned by the XComponent and handed over by napi_bridge.c;
 *   (b) vkCreateSurfaceOHOS (VK_OHOS_surface), from the SDK's own <vulkan/vulkan_ohos.h>;
 *   (c) not used: pool_zero_copy() fails at AHardwareBuffer_allocate() and the backend falls back to the (slower but
 *       correct) copied pools. A native-buffer import path can replace it later without touching the replay code.
 * The shims below keep the shared sources compiling unchanged; nothing here is meant to work by itself.
 */
#pragma once
#if !defined(DM_OHOS)
#error "ohos_vk_platform.h is the HarmonyOS side of dm/android/gfx_vk.h"
#endif

#include <native_window/external_window.h>   /* OHNativeWindow */
#include <vulkan/vulkan.h>
#include <vulkan/vulkan_ohos.h>

/* The backend names the window type ANativeWindow (dm_front_window); here it is the XComponent's OHNativeWindow. */
typedef OHNativeWindow ANativeWindow;
ANativeWindow *dm_front_window(void);   // front_ohos.c: the XComponent's native window, NULL until it exists

/* The window size the backend asks for: whoever owns the XComponent records it (front_ohos.c). 0 = no window yet. */
void dm_ohos_surface_size(int *w, int *h);
static inline int ANativeWindow_getWidth(ANativeWindow *w) { int a = 0, b = 0; (void)w; dm_ohos_surface_size(&a, &b); return a; }
static inline int ANativeWindow_getHeight(ANativeWindow *w) { int a = 0, b = 0; (void)w; dm_ohos_surface_size(&a, &b); return b; }
/* Android's compositor picks the frame rate: nothing to do here (the present mode paces the swapchain). */
static inline int ANativeWindow_setFrameRate(ANativeWindow *w, float fps, int compat) { (void)w; (void)fps; (void)compat; return 0; }
#define ANATIVEWINDOW_FRAME_RATE_COMPATIBILITY_FIXED_SOURCE 0

/* ---- AHardwareBuffer: not available; every call fails, so the pool stays a copied one ---- */
typedef struct AHardwareBuffer AHardwareBuffer;
typedef struct {
    uint32_t width, height, layers, format;
    uint64_t usage;
    uint32_t stride, rfu0;
    uint64_t rfu1;
} AHardwareBuffer_Desc;
#define AHARDWAREBUFFER_FORMAT_BLOB 0x21
#define AHARDWAREBUFFER_USAGE_CPU_READ_OFTEN 1ull
#define AHARDWAREBUFFER_USAGE_CPU_WRITE_OFTEN 2ull
#define AHARDWAREBUFFER_USAGE_GPU_DATA_BUFFER 0x100ull
static inline int AHardwareBuffer_allocate(const AHardwareBuffer_Desc *d, AHardwareBuffer **out) { (void)d; if (out) *out = 0; return -1; }
static inline void AHardwareBuffer_release(AHardwareBuffer *b) { (void)b; }
static inline int AHardwareBuffer_sendHandleToUnixSocket(const AHardwareBuffer *b, int fd) { (void)b; (void)fd; return -1; }

/* The Vulkan Android-import structures the shared sources name (dead code here: pool_zero_copy returns before them). */
typedef struct VkAndroidHardwareBufferPropertiesANDROID {
    VkStructureType sType; const void *pNext; VkDeviceSize allocationSize; uint32_t memoryTypeBits;
} VkAndroidHardwareBufferPropertiesANDROID;
typedef struct VkImportAndroidHardwareBufferInfoANDROID {
    VkStructureType sType; const void *pNext; AHardwareBuffer *buffer;
} VkImportAndroidHardwareBufferInfoANDROID;
#define VK_STRUCTURE_TYPE_ANDROID_HARDWARE_BUFFER_PROPERTIES_ANDROID ((VkStructureType)1000129000)
#define VK_STRUCTURE_TYPE_IMPORT_ANDROID_HARDWARE_BUFFER_INFO_ANDROID ((VkStructureType)1000129001)
#define VK_EXTERNAL_MEMORY_HANDLE_TYPE_ANDROID_HARDWARE_BUFFER_BIT_ANDROID 0x00000400ull
static inline VkResult vkGetAndroidHardwareBufferPropertiesANDROID(VkDevice d, AHardwareBuffer *b, VkAndroidHardwareBufferPropertiesANDROID *p) {
    (void)d; (void)b; (void)p; return VK_ERROR_INITIALIZATION_FAILED;
}
