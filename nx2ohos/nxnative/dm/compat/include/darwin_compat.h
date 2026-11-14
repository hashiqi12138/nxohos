// Android port: force-included (-include) into every host source. Darwin library calls with a direct bionic counterpart.
#pragma once
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include <pthread.h>
#include <stddef.h>
#include <stdint.h>
#include <unistd.h>
#include <sys/mman.h>

#ifndef MAP_ANON
#define MAP_ANON MAP_ANONYMOUS
#endif
// Darwin's pthread_setname_np names only the calling thread (one argument); Linux takes the thread too
int dm_setname_self(const char *name);
#define DM_SETNAME_PICK(_1, _2, NAME, ...) NAME
#define pthread_setname_np(...) DM_SETNAME_PICK(__VA_ARGS__, pthread_setname_np, dm_setname_self, x)(__VA_ARGS__)
// the calling thread's stack: Darwin returns its top (highest address) and size
void *pthread_get_stackaddr_np(pthread_t t);
size_t pthread_get_stacksize_np(pthread_t t);
int pthread_threadid_np(pthread_t t, uint64_t *id);
#if !defined(__OHOS__)   // OHOS libc has getdtablesize itself
static inline int getdtablesize(void) { return (int)sysconf(_SC_OPEN_MAX); }
#endif
#include <stdbool.h>   // the Darwin headers pull this in transitively; musl's do not
#include <time.h>
#ifndef DM_CLOCK_NSEC_NP
#define DM_CLOCK_NSEC_NP
static inline uint64_t clock_gettime_nsec_np(clockid_t c) { struct timespec t; if (clock_gettime(c, &t)) return 0; return (uint64_t)t.tv_sec * 1000000000ull + (uint64_t)t.tv_nsec; }
#endif
static inline size_t malloc_good_size(size_t n) { return (n + 15) & ~(size_t)15; }
#include <pthread/qos.h>   // Darwin's <pthread.h> brings the QoS classes with it
// a wait with a relative timeout (a default condition variable measures CLOCK_REALTIME)
static inline int pthread_cond_timedwait_relative_np(pthread_cond_t *c, pthread_mutex_t *m, const struct timespec *rel) {
    struct timespec t; clock_gettime(CLOCK_REALTIME, &t);
    t.tv_sec += rel->tv_sec; t.tv_nsec += rel->tv_nsec; if (t.tv_nsec >= 1000000000L) { t.tv_sec++; t.tv_nsec -= 1000000000L; }
    return pthread_cond_timedwait(c, m, &t);
}
#define MACH_PORT_NULL 0

#if !defined(__APPLE__)
#include <stdlib.h>
#include <fcntl.h>
// Darwin's stat names the timespec fields st_*timespec; musl names them st_*tim
#define st_mtimespec st_mtim
#define st_atimespec st_atim
#define st_ctimespec st_ctim
// BSD mergesort: qsort orders by the same comparator (not stable, which the host's step sort tolerates)
#define mergesort(base, nel, width, compar) qsort((base), (nel), (width), (compar))
// Apple's JIT mapping flag and write-protect toggle: OHOS maps the pages directly and has no toggle
#ifndef MAP_JIT
#define MAP_JIT 0
#endif
static inline void pthread_jit_write_protect_np(int enabled) { (void)enabled; }
// libproc's rusage: not available on OHOS; the guest-file layer treats a failure as "stay anonymous" (hle_svc.c)
typedef void *rusage_info_t;
#ifndef RUSAGE_INFO_V4
#define RUSAGE_INFO_V4 4
#endif
int proc_pid_rusage(int pid, int flavor, rusage_info_t *buffer);
// F_PUNCHHOLE has no fcntl counterpart on Linux: a command the kernel rejects, so guest_file_punch keeps the blocks (it handles the failure)
typedef struct { off_t fp_offset; off_t fp_length; } fpunchhole_t;
#ifndef F_PUNCHHOLE
#define F_PUNCHHOLE 0x10001
#endif
// mincore's dirty bit: Linux mincore only reports residency (bit 0), so treat a resident page as modified (a conservative overcount for the guest-file diagnostics, hle_svc.c)
#ifndef MINCORE_MODIFIED
#define MINCORE_MODIFIED 1
#endif
// The path a descriptor points at (hle_fdstat.c): Darwin's fcntl(F_GETPATH), Linux's /proc/self/fd readlink
#include <stdio.h>
static inline int dm_fd_path(int fd, char *buf, size_t n) {
#if defined(__APPLE__)
    return fcntl(fd, F_GETPATH, buf);
#else
    char ln[64]; snprintf(ln, sizeof ln, "/proc/self/fd/%d", fd); ssize_t r = readlink(ln, buf, n - 1); if (r < 0) return -1; buf[r] = 0; return 0;
#endif
}
// MacTypes' UInt8 (hle_svc.c casts a path to it for CFURLCreateFromFileSystemRepresentation)
typedef uint8_t UInt8;
// Darwin's statfs / fstatfs (hle_svc.c guest-file disk room): OHOS has statvfs instead. f_fstypename / MNT_LOCAL are
// filled so the "local APFS" diagnostic stays quiet; the guest-file layer is optional (DM_GUEST_FILE) and OHOS runs anonymous by default
#include <sys/statvfs.h>
#include <string.h>
#ifndef MNT_LOCAL
#define MNT_LOCAL 0x1000
#endif
#ifndef DM_COMPAT_STATFS
#define DM_COMPAT_STATFS
struct statfs { long f_bsize, f_bavail, f_blocks; unsigned long f_flags; char f_fstypename[16]; };
static inline int fstatfs(int fd, struct statfs *s) {
    struct statvfs v; if (fstatvfs(fd, &v)) return -1;
    s->f_bsize = (long)v.f_frsize; s->f_bavail = (long)v.f_bavail; s->f_blocks = (long)v.f_blocks;
    s->f_flags = MNT_LOCAL; memcpy(s->f_fstypename, "apfs", 5); return 0;
}
#endif
// libproc's rusage_info_v4 fields the guest-file diagnostics read; proc_pid_rusage fails (ENOTSUP) so they stay zero
struct rusage_info_v4 { uint64_t ri_diskio_bytesread, ri_pageins, ri_logical_writes, ri_lifetime_max_phys_footprint; };
// confstr name for the per-user temp dir: unknown here, so confstr returns 0 and guest_file_init stays anonymous
#ifndef _CS_DARWIN_USER_TEMP_DIR
#define _CS_DARWIN_USER_TEMP_DIR 0xFFFF
#endif
#endif
