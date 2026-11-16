// OHOS port: the diag samplers' stub (DM_MEMSTAT / DM_THRCPU, upstream memstat.m).
//
// Upstream's host_preload (main.c) calls dm_diag_samplers_start() after the front end's env files are read; the
// implementation lives in memstat.m, an Objective-C file the OHOS build does not compile (it builds host/*.c).
// The link failed on the first upstream sync that brought the call in - "undefined symbol: dm_diag_samplers_start"
// - and the diagnostics themselves are Apple-side (task_vm_info, thread_info energy) that this platform has no
// answer for. So the port answers with the no-op: the samplers stay off, the call site is satisfied, and the
// switches (DM_MEMSTAT / DM_THRCPU) simply read as off.
#include <stdatomic.h>

void dm_diag_samplers_start(void) {
    static atomic_int once;
    if (atomic_exchange(&once, 1)) return;
    // no samplers on this platform: DM_MEMSTAT / DM_THRCPU stay off
}
