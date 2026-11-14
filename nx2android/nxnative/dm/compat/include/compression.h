// Android/OHOS port: Apple's libcompression (LZFSE). nvn_capture.c compresses capture chunks with it; here encode/decode
// are plain copies, so cap_put sees no size reduction and stores CAP_RAW, which cap_next reads back the same way. Captures work, uncompressed.
#pragma once
#include <stddef.h>
#include <stdint.h>
#include <string.h>
#define COMPRESSION_LZFSE 0x801
#define COMPRESSION_LZ4 0x100
#define COMPRESSION_ZLIB 0x200
static inline size_t compression_encode_buffer(uint8_t *dst, size_t dstn, const uint8_t *src, size_t srcn, void *work, int algo) {
    (void)work; (void)algo; if (srcn > dstn) return 0; memcpy(dst, src, srcn); return srcn;
}
static inline size_t compression_decode_buffer(uint8_t *dst, size_t dstn, const uint8_t *src, size_t srcn, void *work, int algo) {
    (void)work; (void)algo; if (srcn > dstn) return 0; memcpy(dst, src, srcn); return srcn;
}