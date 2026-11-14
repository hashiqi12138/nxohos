// Android port: Darwin's <mach-o/dyld.h> (image slides / names). The host includes it but the OHOS loader does not use dyld; nothing to provide.
#pragma once
#include <stdint.h>
static inline intptr_t _dyld_get_image_vmaddr_slide(uint32_t i) { (void)i; return 0; }
static inline uint32_t _dyld_image_count(void) { return 0; }
static inline const char *_dyld_get_image_name(uint32_t i) { (void)i; return 0; }