// HarmonyOS side of the H.264 decoder (the counterpart of dm/android/movie_avc.c, which feeds MediaCodec): nothing yet — the OHOS video
// decoder (OH_VideoDecoder, AVCodec) is M3.5 work. Movies are the only caller (hle_movie.c), and a game that has no movie never decodes a
// frame, so this returns "no picture" and the video simply stays black until that milestone. Every entry point matches hle_movie_avc.h.
#include <stdlib.h>
#include <string.h>
#include "hle_movie_avc.h"

struct mavc { int unused; };
mavc *mavc_new(void) { return calloc(1, sizeof(mavc)); }
void mavc_free(mavc *a) { free(a); }
void mavc_param_sets(mavc *a, const uint8_t *p, size_t n) { (void)a; (void)p; (void)n; }
void mavc_avcc(mavc *a, const uint8_t *p, size_t n) { (void)a; (void)p; (void)n; }
int mavc_decode(mavc *a, const uint8_t *p, int n, int64_t pts_us) { (void)a; (void)p; (void)n; (void)pts_us; return 0; }
int mavc_take(mavc *a, int flush, uint8_t *nv12, int w, int h, int64_t *pts_us) { (void)a; (void)flush; (void)nv12; (void)w; (void)h; (void)pts_us; return 0; }
int mavc_queued(const mavc *a) { (void)a; return 0; }
int mavc_held(const mavc *a) { (void)a; return 0; }
void mavc_set_rgba(mavc *a) { (void)a; }
void mavc_drop(mavc *a) { (void)a; }
