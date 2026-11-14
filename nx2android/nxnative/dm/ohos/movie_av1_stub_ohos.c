// OHOS port: AV1 movie decoding is off. hle_movie_av1.c needs VideoToolbox / dav1d and is excluded from the OHOS build;
// these no-ops satisfy hle_movie_av1.h so the nn::codec video take-over (hle_codec_video.c) links. mav1_new gives NULL,
// every decode reports "refused" (-1), so a game falls back as it does when no decoder is present.
#include "../host/hle_movie_av1.h"

mav1 *mav1_new(void) { return NULL; }
void mav1_free(mav1 *a) { (void)a; }
void mav1_reset(mav1 *a) { (void)a; }
int mav1_decode(mav1 *a, const uint8_t *data, size_t n, int64_t ts, mav1_picture *out) { (void)a; (void)data; (void)n; (void)ts; (void)out; return -1; }
int mav1_next(mav1 *a, int drain, mav1_picture *out) { (void)a; (void)drain; (void)out; return 0; }
const char *mav1_backend(const mav1 *a) { (void)a; return "none"; }
int mav1_av1c(const uint8_t *obu, size_t n, uint8_t c[4], int *w, int *h) { (void)obu; (void)n; (void)c; (void)w; (void)h; return 0; }