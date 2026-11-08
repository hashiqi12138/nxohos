/* vk_ohos_shim.h — OHOS Vulkan WSI 接入点（真 SDK 头直用，不做占位）
 *
 * 实测（OHOS SDK，clang 15.0.4）：`<vulkan/vulkan.h>` 已自动包含
 * `<vulkan/vulkan_ohos.h>`，直接提供：
 *   #define VK_OHOS_surface 1
 *   typedef struct NativeWindow OHNativeWindow;
 *   typedef struct VkSurfaceCreateInfoOHOS {
 *       VkStructureType sType;            // VK_STRUCTURE_TYPE_SURFACE_CREATE_INFO_OHOS
 *       const void*     pNext;            // 必须 NULL
 *       VkSurfaceCreateFlagsOHOS flags;   // 必须 0
 *       OHNativeWindow* window;
 *   } VkSurfaceCreateInfoOHOS;
 *   typedef VkResult (VKAPI_PTR *PFN_vkCreateSurfaceOHOS)(
 *       VkInstance, const VkSurfaceCreateInfoOHOS*,
 *       const VkAllocationCallbacks*, VkSurfaceKHR*);
 *   VKAPI_ATTR VkResult VKAPI_CALL vkCreateSurfaceOHOS(...);   // since 10
 *
 * 结论：早期版本本文件里的占位结构（VkSurfaceOHOSCreateInfoOHOS /
 * VK_STRUCTURE_TYPE_SURFACE_OHOS_CREATE_INFO_OHOS）是真名记错，已删除。
 * 若今后 SDK 缺少该扩展，这里直接 #error —— 宁可构建失败，也不要用占位值
 * 编出一个真机行为不可信的探针（B 文档「依赖与真实边界」1）。
 */
#ifndef VK_OHOS_SHIM_H
#define VK_OHOS_SHIM_H

#include <vulkan/vulkan.h>
#include <vulkan/vulkan_ohos.h>   /* 实测：vulkan.h 不会自动带出该扩展头，须显式包含 */

#ifndef VK_OHOS_surface
#error "当前 SDK 的 <vulkan/vulkan.h> 未提供 VK_OHOS_surface —— 请确认使用 OHOS SDK 而非上游 Vulkan SDK"
#endif

/* VK_STRUCTURE_TYPE_SURFACE_CREATE_INFO_OHOS 是 enum VkStructureType 的成员
 * （= 1000685000），不是宏，预处理无法用 #ifndef 检测——故此处不做预处理断言；
 * 缺失时会在 probe_vk.c 的初始化处直接编译报错，同样是构建期暴露。 */

#endif /* VK_OHOS_SHIM_H */
