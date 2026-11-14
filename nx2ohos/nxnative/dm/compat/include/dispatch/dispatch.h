// Android port: the counting semaphores of libdispatch on POSIX semaphores. Queues, sources and blocks are not provided (the host's few uses are ported in place).
#pragma once
#include <semaphore.h>
#include <stdint.h>
#include <stdlib.h>
#include <errno.h>
#include <time.h>
#include <pthread.h>
#include <pthread/qos.h>
typedef sem_t *dispatch_semaphore_t;
typedef uint64_t dispatch_time_t;
#define DISPATCH_TIME_NOW 0ull
#define DISPATCH_TIME_FOREVER (~0ull)
#ifndef NSEC_PER_SEC
#define NSEC_PER_SEC 1000000000ull
#define NSEC_PER_MSEC 1000000ull
#define NSEC_PER_USEC 1000ull
#endif
// a dispatch_time is an absolute CLOCK_REALTIME instant in ns (what sem_timedwait takes)
static inline dispatch_time_t dispatch_time(dispatch_time_t when, int64_t delta) {
    if (when == DISPATCH_TIME_FOREVER) return when;
    if (when == DISPATCH_TIME_NOW) { struct timespec t; clock_gettime(CLOCK_REALTIME, &t); when = (uint64_t)t.tv_sec * NSEC_PER_SEC + (uint64_t)t.tv_nsec; }
    return when + (uint64_t)delta;
}
static inline dispatch_semaphore_t dispatch_semaphore_create(long v) { sem_t *s = malloc(sizeof *s); sem_init(s, 0, (unsigned)v); return s; }
static inline long dispatch_semaphore_signal(dispatch_semaphore_t s) { sem_post(s); return 0; }
static inline long dispatch_semaphore_wait(dispatch_semaphore_t s, dispatch_time_t t) {
    if (t == DISPATCH_TIME_FOREVER) { while (sem_wait(s) && errno == EINTR) ; return 0; }
    struct timespec ts = {(time_t)(t / NSEC_PER_SEC), (long)(t % NSEC_PER_SEC)};
    for (;;) { if (!sem_timedwait(s, &ts)) return 0; if (errno != EINTR) return 1; }
}
static inline void dispatch_release(void *o) { if (o) { sem_destroy((sem_t *)o); free(o); } }
// The host's one queue use (hle_audio.c au_request_start): dispatch_async_f on a global concurrent queue runs a C function
// on a detached thread. A queue is a dummy handle; the global queue is concurrent, so a thread per call matches its semantics.
typedef void *dispatch_queue_t;
typedef void (*dispatch_function_t)(void *);
#define DISPATCH_QUEUE_SERIAL NULL
static inline dispatch_queue_t dispatch_get_global_queue(long qos, unsigned long flags) { (void)qos; (void)flags; return (dispatch_queue_t)1; }
struct dispatch_async_args { dispatch_function_t fn; void *ctx; };
static inline void *dispatch_async_th(void *p) { struct dispatch_async_args *a = p; dispatch_function_t fn = a->fn; void *ctx = a->ctx; free(a); fn(ctx); return NULL; }
static inline void dispatch_async_f(dispatch_queue_t q, void *ctx, dispatch_function_t fn) {
    (void)q; struct dispatch_async_args *a = malloc(sizeof *a); if (!a) { fn(ctx); return; } a->fn = fn; a->ctx = ctx;
    pthread_t t; if (!pthread_create(&t, NULL, dispatch_async_th, a)) pthread_detach(t); else { fn(ctx); free(a); }
}
