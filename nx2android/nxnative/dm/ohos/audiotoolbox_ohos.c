// HarmonyOS port of the AudioUnit output unit (the counterpart of dm/android/compat/audiotoolbox_aaudio.c): an OHAudio renderer whose
// data callback pulls the host's render callback into one AudioBuffer, so hle_audio.c's pull code is unchanged. The stop/start handling is
// the Android port's: a stop holds the stream (silence, and the host's output is never entered), so a pause or a background trip resumes on
// the same stream; a stream that failed or stalled is opened again by the next start (the host's watchdog retries).
#include <AudioToolbox/AudioToolbox.h>
#include <ohaudio/native_audiorenderer.h>
#include <ohaudio/native_audiostreambuilder.h>
#include <ohaudio/native_audiostream_base.h>
#include <sched.h>
#include <stdatomic.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include "dm.h"

struct dm_audio_component { int unused; };
struct dm_audio_unit {
    AudioStreamBasicDescription fmt; int have_fmt;
    AURenderCallbackStruct cb;
    int32_t frames_hint;
    OH_AudioRenderer *st;
    _Atomic uint64_t sample_time;
    uint64_t stop_time;      // sample_time at the last stop: unchanged at the next start = the stream stopped calling back
    _Atomic int failed;      // the renderer reported an error: the next start opens a new stream
    _Atomic int held, in_cb; // held: stopped (silence); in_cb: callbacks inside the host's render callback
    uint64_t held_frames;
};
static struct dm_audio_component g_output;
enum { HOLD_MAX_S = 60 };

AudioComponent AudioComponentFindNext(AudioComponent after, const AudioComponentDescription *d) {
    return !after && d && d->componentType == kAudioUnitType_Output ? &g_output : NULL;
}
OSStatus AudioComponentInstanceNew(AudioComponent c, AudioComponentInstance *out) {
    if (c != &g_output) return kAudio_UnimplementedError;
    *out = calloc(1, sizeof **out); return *out ? noErr : -1;
}
OSStatus AudioUnitSetProperty(AudioUnit u, AudioUnitPropertyID id, AudioUnitScope scope, AudioUnitElement el, const void *data, UInt32 size) {
    (void)scope; (void)el;
    switch (id) {
    case kAudioUnitProperty_StreamFormat:
        if (size < sizeof u->fmt) return -50;
        memcpy(&u->fmt, data, sizeof u->fmt);
        if (u->fmt.mFormatID != kAudioFormatLinearPCM || (u->fmt.mFormatFlags & kAudioFormatFlagIsNonInterleaved)) return kAudioUnitErr_FormatNotSupported;
        if (!(u->fmt.mFormatFlags & kAudioFormatFlagIsFloat) && u->fmt.mBitsPerChannel != 16) return kAudioUnitErr_FormatNotSupported;
        u->have_fmt = 1; return noErr;
    case kAudioUnitProperty_SetRenderCallback: if (size < sizeof u->cb) return -50; memcpy(&u->cb, data, sizeof u->cb); return noErr;
    case kAudioDevicePropertyBufferFrameSize: if (size >= 4) u->frames_hint = (int32_t)*(const UInt32 *)data; return noErr;
    default: return noErr;
    }
}
OSStatus AudioUnitInitialize(AudioUnit u) { return u->have_fmt && u->cb.inputProc ? noErr : -50; }

// one callback: fill audioData with the host's samples (or silence while held)
static OH_AudioData_Callback_Result on_write(OH_AudioRenderer *r, void *ud, void *audio, int32_t bytes) {
    (void)r; AudioUnit u = ud;
    int frame = (int)u->fmt.mBytesPerFrame; if (frame <= 0) return AUDIO_DATA_CALLBACK_RESULT_INVALID;
    int frames = bytes / frame;
    atomic_fetch_add(&u->in_cb, 1);
    if (atomic_load(&u->held)) {
        memset(audio, 0, (size_t)bytes); atomic_fetch_add(&u->sample_time, (uint64_t)frames);
        int end = (u->held_frames += (uint64_t)frames) > (uint64_t)(u->fmt.mSampleRate * HOLD_MAX_S);
        atomic_fetch_sub(&u->in_cb, 1); (void)end;
        return AUDIO_DATA_CALLBACK_RESULT_VALID;
    }
    AudioBufferList bl = {1, {{u->fmt.mChannelsPerFrame, (UInt32)bytes, audio}}};
    AudioUnitRenderActionFlags fl = 0;
    struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t);
    AudioTimeStamp ts = {.mSampleTime = (Float64)atomic_fetch_add(&u->sample_time, (uint64_t)frames), .mHostTime = (UInt64)t.tv_sec * 1000000000ull + (UInt64)t.tv_nsec};
    if (u->cb.inputProc(u->cb.inputProcRefCon, &fl, &ts, 0, (UInt32)frames, &bl)) memset(audio, 0, (size_t)bytes);
    atomic_fetch_sub(&u->in_cb, 1);
    return AUDIO_DATA_CALLBACK_RESULT_VALID;
}
static void on_err(OH_AudioRenderer *r, void *ud, OH_AudioStream_Result err) {
    (void)r; atomic_store(&((AudioUnit)ud)->failed, 1);
    LOG("[audio] OHAudio renderer error %d: the host's watchdog restarts it", (int)err);
}

static void close_stream(AudioUnit u) { if (u->st) { OH_AudioRenderer_Release(u->st); u->st = NULL; } }

OSStatus AudioOutputUnitStart(AudioUnit u) {
    // A stop and start goes on with the same renderer (it played silence meanwhile): a new stream would be a new audio track, and on Android that
    // changed the mixing path and the loudness (see audiotoolbox_aaudio.c). Only a failed or stalled renderer is opened again.
    if (u->st && !atomic_load(&u->failed) && (!atomic_load(&u->held) || atomic_load(&u->sample_time) != u->stop_time)) {
        atomic_store(&u->held, 0); return noErr;
    }
    close_stream(u);
    atomic_store(&u->failed, 0); atomic_store(&u->held, 0);
    OH_AudioStreamBuilder *b = NULL;
    if (OH_AudioStreamBuilder_Create(&b, AUDIOSTREAM_TYPE_RENDERER) != AUDIOSTREAM_SUCCESS) return -1;
    OH_AudioStreamBuilder_SetSamplingRate(b, (int32_t)u->fmt.mSampleRate);
    OH_AudioStreamBuilder_SetChannelCount(b, (int32_t)u->fmt.mChannelsPerFrame);
    OH_AudioStreamBuilder_SetSampleFormat(b, u->fmt.mFormatFlags & kAudioFormatFlagIsFloat ? AUDIOSTREAM_SAMPLE_F32LE : AUDIOSTREAM_SAMPLE_S16LE);
    OH_AudioStreamBuilder_SetLatencyMode(b, AUDIOSTREAM_LATENCY_MODE_FAST);
    OH_AudioStreamBuilder_SetRendererWriteDataCallback(b, on_write, u);
    OH_AudioStreamBuilder_SetRendererErrorCallback(b, on_err, u);
    OH_AudioStream_Result r = OH_AudioStreamBuilder_GenerateRenderer(b, &u->st);
    OH_AudioStreamBuilder_Destroy(b);
    if (r != AUDIOSTREAM_SUCCESS || !u->st) { u->st = NULL; LOG("[audio] no renderer (%d)", (int)r); return r ? r : -1; }
    r = OH_AudioRenderer_Start(u->st);
    if (r != AUDIOSTREAM_SUCCESS) { close_stream(u); LOG("[audio] renderer will not start (%d)", (int)r); return r; }
    return noErr;
}
OSStatus AudioOutputUnitStop(AudioUnit u) {
    if (!u->st) return noErr;
    u->held_frames = 0; atomic_store(&u->held, 1);   // set before in_cb is read: no new callback enters the host once it is set
    while (atomic_load(&u->in_cb)) sched_yield();
    u->stop_time = atomic_load(&u->sample_time); return noErr;
}
OSStatus AudioUnitUninitialize(AudioUnit u) { close_stream(u); return noErr; }
OSStatus AudioComponentInstanceDispose(AudioComponentInstance u) { if (u) { close_stream(u); free(u); } return noErr; }
// Mac DefaultOutput device tuning has no OHOS counterpart: no device object to query (GetProperty fails, so hle_audio.c's
// au_buffer leaves the renderer's default buffer), and no property change notifications (the listeners are kept silent).
OSStatus AudioUnitGetProperty(AudioUnit u, AudioUnitPropertyID id, AudioUnitScope scope, AudioUnitElement el, void *data, UInt32 *size) {
    (void)u; (void)id; (void)scope; (void)el; (void)data; (void)size; return -1;
}
OSStatus AudioUnitAddPropertyListener(AudioUnit u, AudioUnitPropertyID id, AudioUnitPropertyListenerProc proc, void *ctx) {
    (void)u; (void)id; (void)proc; (void)ctx; return noErr;
}
OSStatus AudioUnitRemovePropertyListenerWithUserData(AudioUnit u, AudioUnitPropertyID id, AudioUnitPropertyListenerProc proc, void *ctx) {
    (void)u; (void)id; (void)proc; (void)ctx; return noErr;
}

// ---- AudioConverter: not yet (the AAC decoder is AudioConverter over MediaCodec on Android; HarmonyOS's AVCodec is M2/M3 work) ----
// hle_movie.c and hle_audio.c's AAC path then get no PCM; a game whose soundtrack is AAC plays silently, everything else is unaffected.
OSStatus AudioConverterNew(const AudioStreamBasicDescription *in, const AudioStreamBasicDescription *out, AudioConverterRef *c) {
    (void)in; (void)out; if (c) *c = NULL; return kAudio_UnimplementedError;
}
OSStatus AudioConverterSetProperty(AudioConverterRef c, AudioConverterPropertyID id, UInt32 size, const void *data) { (void)c; (void)id; (void)size; (void)data; return kAudio_UnimplementedError; }
OSStatus AudioConverterFillComplexBuffer(AudioConverterRef c, AudioConverterComplexInputDataProc proc, void *ref, UInt32 *packets, AudioBufferList *out, AudioStreamPacketDescription *desc) {
    (void)c; (void)proc; (void)ref; (void)out; (void)desc; if (packets) *packets = 0; return kAudio_UnimplementedError;
}
OSStatus AudioConverterDispose(AudioConverterRef c) { (void)c; return noErr; }
