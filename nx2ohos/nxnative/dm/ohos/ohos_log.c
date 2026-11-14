// The host's LOG() on OHOS. stderr is not readable in an app process, so every line goes to hilog with a domain and tag of its own; the
// AGC cloud console exports that log, which is how a run on the device is judged. Keep the tag short: it is the column the export greps.
#include <hilog/log.h>
#include <stdarg.h>
#include <stdio.h>

#undef LOG_DOMAIN
#undef LOG_TAG
// hilog only accepts the 0x0000-0xFFFF domain range for apps: a line tagged 0xD0021 was accepted by OH_LOG_Print but never reached the
// buffer (nothing from the native side showed up in the cloud export while the ArkTS logs at 0x0000 did).
#define LOG_DOMAIN 0x0201
#define LOG_TAG "dmhost"

void dm_ohos_logv(const char *fmt, ...) {
    char b[1024];
    va_list ap; va_start(ap, fmt); vsnprintf(b, sizeof b, fmt, ap); va_end(ap);
    OH_LOG_INFO(LOG_APP, "%{public}s", b);
}
