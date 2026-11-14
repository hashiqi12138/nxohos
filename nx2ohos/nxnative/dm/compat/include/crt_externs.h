// Android port: Darwin's <crt_externs.h> exposes the environment through _NSGetEnviron(); musl exposes `environ` directly.
#pragma once
#include <stdint.h>
extern char **environ;
static inline char ***_NSGetEnviron(void) { return &environ; }
// The executable path and the argument vector, from /proc/self (darwin_compat.c). The host relaunches itself for a program switch (hle_oe.c)
int _NSGetExecutablePath(char *buf, uint32_t *size);
char ***_NSGetArgv(void);
int *_NSGetArgc(void);