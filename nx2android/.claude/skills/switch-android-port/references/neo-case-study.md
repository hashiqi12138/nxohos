# Case study: NEO: The World Ends with You on Android

**Target:**
- Title 010043B013C5C000, update 1.03 (v196608).
- Unity 2019.4.16f1 IL2CPP on NVN, SDK 10.4.1, CRIWARE and FMOD.
- Modules: main, rtld, sdk, subsdk0. RomFS: 6.3 GB in 6,299 files.

**Device:** Lenovo Legion Y700 TB322FC.
- Snapdragon 8 Elite: 6 cores at 3.53 GHz and 2 at 4.32 GHz. Adreno 830, Vulkan 1.3.
- 12 GB RAM, Android 16, 3040x1904 screen.

**Duration:** from feasibility study to a playable app in about a day (24–25 Oct 2026, UTC+7). Two 30-minute soaks and a 14-minute session by the user ran without a crash.

The phases are in order. Each records what was done, what broke, how it was found, and what fixed it.

## A. Research and device probes (about 1.5 h)
- `RESEARCH.md` was written from reading the code only. Verdict: the rewriter and most of the host carry over; the 842 KB NVN-on-Metal layer (`nvn_mtl.m`) needs a Vulkan rewrite.
- Three probes (`probes/cpuprobe.c`, `vkprobe.c`, `vkzc.c`) ran from `adb shell` and inside a NativeActivity APK. Results were identical in both.
  - The kernel keeps x18; `cntpct_el0` raises SIGILL; a misaligned sp raises SIGBUS.
  - cntfrq is 19.2 MHz, the same as the Switch. The largest PROT_NONE reservation is 256 GB.
  - BC1–7 textures are supported in hardware. `VK_EXT_external_memory_host` is missing.
  - **The deciding result:** an AHardwareBuffer's dma-buf can be `mmap(MAP_FIXED)` at the guest address and used by Vulkan. That gives zero-copy, cached, coherent guest memory pools (0 wrong words in 2 × 64 MB).
- `probes/app/mkapk.py` builds and v2-signs an APK with the Python standard library only: no JDK, no Android SDK. Its descendant is `dm/android/apk/mkapk.py`.

## B. Toolchain, extraction, rewrite (about 15 min)
- Installed the .NET 10 SDK and `nsz`. NSZ decompression gave a 6.7 GB base and a 517 MB update; extraction took 25 s.
- `patch_nso.py` sites and host gates per module: main 18.5k sites / 31 gates (61 s), subsdk0 10k / 13, sdk 11.5k / 102. No asserts.
- **Android rewrite target:** `tpidrro_el0` reads 0 on Android, so guest thread slots go through `mrs tpidr_el0; ldr x,[x,#16]` (bionic's `TLS_SLOT_APP`).
  - `fixups.tsv` records the target and the loader refuses a mismatch.
  - The Apple output stayed byte-identical.

## C. Headless host (about 20 min)
- The compat layer (`dm/android/compat`) covers mach calls, `getsectiondata` via `__start_`/`__stop_`, an AudioUnit → AAudio shim, xattr and clonefile.
- `dm_uc.h` maps Linux sigcontext to Darwin's thread state. `darwin2elf.py` converts the assembly. `build.py` replaces make.
- **Link result:** 73 missing symbols, about 62 of them `mtl_*`. That list is the backend's contract. A null GPU backend was written first.
- **Boot 1:** `svcBreak` in nninitStartup. The ~3 GB StandardAllocator alias region's alignment depended on ASLR placement. Fix: over-reserve and trim to 1 GB alignment (`host/hle_svc.c`).
- **Boot 2:** reached nnMain, NVN init, the save mount and 53 frames, then a null-pointer crash. Cause: RomFS not fully pushed yet. Gone once the files were complete (inferred, not proven separately).

## D. Shaders and the Vulkan backend, written while adb was down (about 1.5 h)
- **Shader split:** 25,523 shaders (13,872 vertex, 9,248 pixel, 2,402 geometry, 1 compute). Unity 2019.4 compute kernels were skipped because no type tree was known.
- **`shader_vk.py`** reuses the shader translator (Ryujinx, `TargetApi.Vulkan`): 25,514 translated, 9 with no output. The bundle is about 450 MB with binary reflection.
  - Later, a translator fix made while porting the next game (call arguments propagated away, `AstOptimizer.cs`) made it 25,523 of 25,523.
- **Backend:** `gfx_vk_core.c`, `_tex.c`, `_shader.c`, `_exec.c`, `_present.c`. Version-1 design:
  - AHB memory pools;
  - one descriptor set per stage;
  - full pipelines and timeline semaphores;
  - whole-pipeline barriers, GENERAL layout, dynamic rendering;
  - lazy pass begin with folded clears;
  - pipeline prewarm and a pipeline cache, a present thread, ADPF hints, and `ANativeWindow_setFrameRate`.

## E. Getting 6.3 GB onto the device (the biggest time sink of day 1)
- Big pushes wedged adbd over USB: "failed to read copy response: EOF", then "offline". It took 6+ replugs.
- Chunking alone didn't help. **`adb push -Z` (compression off)** did: 35–80 MB/s. Why compression broke it is unknown.
- Switched to Wi-Fi adb (`adb tcpip 5555`), which was steadier.

## F. First frames, headless (about 30 min)
- **No frames:** presents with nothing drawn never completed their ticket, so each waited out a 1 s timeout.
- **Every draw skipped ("no program"):** NVN binds vertex and fragment programs separately by stage mask, and the replay replaced every stage on each bind.
- **First frame:** the publisher logo, 16.66 ms, about 22 draws. Then the intro and the title screen, both correct.

## G. In the app (minutes)
- **App-only SIGSEGV at main+0x2a59b4:** the IL2CPP garbage collector scans the main thread's stack from its reported bottom, and with bionic's own stack that range included an unreadable guard page. Fix: mmap a 16 MB stack with a 64 KB guard and pass it with `pthread_attr_setstack` (`front_android.c`).
- `adb shell input tap` was too short to register; a 250 ms `input swipe` hold works.
- The first 3D scene ran at 53–81 ms a frame: 145 passes for 148 draws.

## H. Correctness (about 6 h, including long waits for the user)

| Symptom | How it was found | Root cause | Fix |
|---|---|---|---|
| Handheld only: `VK_ERROR_OUT_OF_HOST_MEMORY` at vkEndCommandBuffer from about draw 712 | on-device auto-bisection found the draw. The cause came only from the **Khronos validation layer** | the image-view cache key ignored usage, so a sampling view was reused as a colour attachment | `attach` added to the view key (`gfx_vk_tex.c`) |
| Fog everywhere, missing ground, brown silhouettes | pass dumps incl. depth, `DM_VK_NOCULL=1` | inverted front-face winding culled the ground, so depth stayed empty and the fog maxed out | winding rule (`gfx_vk_exec.c`) |
| Docked 3D scene upside down; UI fine | screenshots, pass dumps of the docked chain | NVN upper-left = clip-space y up. Every copy flipped; docked has an odd number of copies | negative-height viewport, unflipped scissor (`DM_VK_FLIPY`). A first try (`pass_h - y`) shifted handheld by 360 rows |
| Detached shadows, flickering distant people | reasoning about deferred uniform updates | shadow copies mixed the newest update with memory a still-running GPU copy hadn't written | in-flight overlay in `vk_ubd_read` (`gfx_vk_core.c`). Visual confirmation uncertain |
| Light-blue 8×8 blocks on near trees, the bus, signals | screencap bursts (1 in 8), then `rec_flicker.py`: up to 58 of 118 frames bad; `DM_VK_CB_DRAWS=0` gave 0 | Adreno LRZ went stale when the frame was cut between passes continuing the same depth buffer | cut only before a pass whose depth image is new in the submission (`depth_sub`); `DM_VK_X_CUTANY=1` restores the old rule |
| 0 draws / black screen in some runs | log | shader bundle looked up before it was opened; misses were remembered | `pthread_once` (`gfx_vk_shader.c`) |

Dead ends on the refused submissions (each tested and ruled out):
- unclipped blits, 8-bit indices, an MSAA mismatch;
- descriptor pool exhaustion, the scratch block boundary, a command-buffer size cap;
- UBWC format lists, the Float64 capability, pixel interlock;
- depth bias, quads, rasterizer discard, zeroed uniform buffers;
- "an Adreno limit of about 712 draws per submission".

Early per-draw tests were also flawed because they changed only draw 712, while later draws failed as well. **Lesson:** when the driver refuses work, run the validation layer before theorising.

Dead ends on the tree flicker: swizzle or re-upload (0 uploads a frame), `ViewportInverse`, a CPU/GPU race (instrumented: 0 racing reads in about 3M), UBWC. NEO's near-black shadows are the game's real style, confirmed against screenshots.

## I. Performance (milestones)

| Change | Before → after |
|---|---|
| defer uniform updates, keep the pass when targets repeat | 145 → 28 passes; 53–81 → 25–30 ms |
| dedupe uniform updates, land them lazily | about 10× fewer copies (267k landed vs 2.6M superseded) |
| submit in pieces so replay overlaps the GPU (`DM_VK_CB_DRAWS` 128, `DM_VK_SUBMIT_SPLIT`) | **36.7 → 15.7 ms**; gameplay about 60 fps median |
| LRZ-safe cut rule (correctness) | about 16 → 19.5 ms (GPU starts later) |
| dynamic uniform-buffer offsets, word-wise hash, texture-view cache | descriptor reuse 6% → 86%; replay 8.5 → 5.3–6.7 ms |
| adaptive governor (60/40/30, display rate matched) | frame start reset on every present (it never ran before); ADPF target kept at the game's period (it was stuck at 30) |
| busiest threads join the ADPF session | docked crowd worst frames 70–90 → 48–58 ms |
| governor judges p90 after 4 s (was p95 after 2 s) | Performance-mode crowd 30 → **40 fps** (p50 24.4–25 ms, p95 34–37 ms) |

**GPU profile** of a crowd frame (`DM_VK_PASS_TIMES`):
- 15–21 ms of GPU time in total.
- The main pass (3 RGBA16F targets, about 480 draws) takes 6–8.5 ms, the 4096² shadow pass about 2 ms, and one full-screen effect 1.4–2 ms.
- Loads and stores cost at most 1 ms, so the cost is the game's own shading.
- Pinned at 40 fps (`DM_FPS_PIN`), GPU busy fell from 20.6 to 15.2 ms as the clocks rose.
- Docked crowds stay at 30 fps. 60 fps in crowds would need lower image quality, which is the user's call.

## J. Front end
- **Sound:** the host used a silent sink because real output defaulted on for iOS only. AAudio then ran with 0 underruns.
- **Icon:** `mkapk.py --icon` writes a minimal `resources.arsc`. The source image stays in the game's work folder.
- **Controllers:** mapped by position (the bottom face button is the Switch's B).
- **On-screen gamepad:** stick knobs follow the finger. Phones scale it from screen density so face buttons are at least 10 mm, capped at ×1.45.
- **Pause menu:** a 70% hamburger button opens it.
  - It holds the game with a guest clock freeze and an audio suspend, and redraws the last frame.
  - Settings: overlay, rumble, Performance (handheld) or Resolution (docked) live via `dm_set_opmode`.
  - Back and Guide also open it; Back used to finish the activity and kill the game.
- **Haptics:** button-press flash and glow; tap ticks and the game's rumble through JNI to `Vibrator` (`haptics_android.c`).
- **Battery:** NEO never imports `nn::psm`, so the tablet's battery can't reach it. Its battery-shaped icon is the level gauge.
- **Release build:** `--release`: stripped, not debuggable, no `DM_CTL`. 1.5 MB APK against 6.3 MB.

## K. The second flicker, a day later
**What the user reported:** "flickering every time camera zoom in-out when story process scene", reproducible by pressing X (menu) then B in free roam.

1. `rec_flicker.py` with an X/B script showed 2 bad frames per transition: 32×32 squares of the old frame drawn flat brown inside the wipe.
2. `DM_VK_CB_DRAWS=0` changed nothing, so this wasn't the cut rule.
3. `env DM_VK_SHOTS=N` saved every presented frame. The brown was in the game's output, in the first two scene frames after the menu's grey frames.
4. Pass dumps never caught a brown frame. Combining shots with a `DM_VK_PASSDUMP=N` countdown showed the dumped frame came out clean while the next was still brown: **the dump hid the bug**.
5. Two theories died on counters along the way: the fallback-texture theory (fallbacks are black) and texture aliasing (0 stale reads).
6. Each piece of the dump was repeated alone:
   - copying the colour target out: still brown;
   - a barrier alone: mixed;
   - **copying the depth buffer out: fixed**;
   - one texel of depth: fixed;
   - one texel only before passes that LOAD a depth-stencil attachment: fixed.
7. **Fix:** `lrz_touch` in `gfx_vk_exec.c`. Result: 2 of 8 bad frames → 0 of 8, GPU and frame time unchanged. Turned off with `DM_VK_LRZ_TOUCH=0` (a ctl `env` line), the brown comes back.

## Pitfalls hit (so you don't)
- **Blind A presses during boot loops advanced the user's story.** Since then, soaks move only the stick and camera.
- **Every launch replayed `ctl.txt` from its first line** (670 old lines). Now the reader starts at the end.
- **A leftover soak loop kept sending moves** after its task was "stopped". Kill the shell process itself.
- **A brightness-based "in gameplay" check passed on the colourful title screen.** Test what is specific to gameplay, twice in a row.
- **The tablet was in the user's hands at times** (Gallery, Camera). Stop and ask.
- **Measurement traps:**
  - dropped submissions left stale GPU timestamps;
  - a stale 93 °C thermal reading (the real sensors read 38–41 °C);
  - runs that reached different scenes, so their numbers weren't comparable;
  - pass timestamps that disturb Adreno's binning.

## Still uncertain
- Whether the detached-shadow fix was ever confirmed visually.
- Why adb's compression broke large pushes.
- Whether the skipped Unity 2019.4 compute kernels matter (nothing visible was reported).
