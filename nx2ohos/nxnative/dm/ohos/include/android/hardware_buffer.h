/* HarmonyOS stand-in for Android's hardware-buffer header: the shared backend (dm/android/gfx_vk_core.c) includes it for the
 * zero-copy pool path; on this platform the allocator fails and the pools stay copied ones. See ohos_vk_platform.h. */
#pragma once
#include "ohos_vk_platform.h"
