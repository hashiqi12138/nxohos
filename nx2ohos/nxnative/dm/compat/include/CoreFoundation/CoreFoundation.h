// Android port: the one CoreFoundation use, a mutable pointer-keyed dictionary with no callbacks (identity keys, values not retained),
// as an open-addressing hash table (dm/android/compat/darwin_compat.c)
#pragma once
#include <stddef.h>
#include <stdint.h>
typedef struct dm_cfdict *CFMutableDictionaryRef, *CFDictionaryRef;
typedef const void *CFAllocatorRef;
typedef long CFIndex;
CFMutableDictionaryRef CFDictionaryCreateMutable(CFAllocatorRef a, CFIndex cap, const void *kcb, const void *vcb);
const void *CFDictionaryGetValue(CFDictionaryRef d, const void *key);
void CFDictionarySetValue(CFMutableDictionaryRef d, const void *key, const void *value);
void CFDictionaryRemoveValue(CFMutableDictionaryRef d, const void *key);
CFIndex CFDictionaryGetCount(CFDictionaryRef d);

// The host reads its build commit from the app bundle's Info.plist (main.c). OHOS has no bundle: CFBundleGetMainBundle returns NULL, so the
// caller takes its "not recorded" branch. The types and calls exist only so that code compiles unchanged.
typedef const void *CFTypeRef, *CFStringRef, *CFBundleRef;
#define CFSTR(s) ((CFStringRef)(s))
#define kCFStringEncodingUTF8 0x08000100ul
static inline CFBundleRef CFBundleGetMainBundle(void) { return NULL; }
static inline CFTypeRef CFBundleGetValueForInfoDictionaryKey(CFBundleRef b, CFStringRef k) { (void)b; (void)k; return NULL; }
static inline unsigned long CFGetTypeID(CFTypeRef v) { (void)v; return 0; }
static inline unsigned long CFStringGetTypeID(void) { return 0; }
static inline int CFStringGetCString(CFStringRef s, char *buf, long n, unsigned long enc) { (void)s; (void)enc; if (n > 0) buf[0] = 0; return 0; }

// Disk free space with purgeable capacity (hle_svc.c guest_file_free_important): no CFURL on OHOS, the URL is NULL so the caller falls back to f_bavail.
typedef const void *CFURLRef, *CFNumberRef;
#define kCFURLVolumeAvailableCapacityForImportantUsageKey CFSTR("NSURLVolumeAvailableCapacityForImportantUsageKey")
#define kCFNumberSInt64Type 4
static inline CFURLRef CFURLCreateFromFileSystemRepresentation(CFAllocatorRef a, const uint8_t *p, CFIndex n, int dir) { (void)a; (void)p; (void)n; (void)dir; return NULL; }
static inline int CFURLCopyResourcePropertyForKey(CFURLRef u, CFStringRef key, CFTypeRef *v, void *err) { (void)u; (void)key; (void)err; if (v) *v = NULL; return 0; }
static inline unsigned long CFNumberGetTypeID(void) { return 0; }
static inline int CFNumberGetValue(CFNumberRef n, int type, void *out) { (void)n; (void)type; (void)out; return 0; }
static inline void CFRelease(CFTypeRef v) { (void)v; }
