
# P0 device verification (2026-11-15)

The emit_tp register-swap fix (key-page TPOFF load must not clobber the thread-pointer register) is verified on the device:
- game thread started, guest thread slots located (pthread keys 23..26, tpidr_el0+672), nnosInitialize passed
- 1900+ frames presented over 18 minutes, present interval switching 2<->3 from game logic (main+0x11267c4)
- 101/101 compute shaders precompiled from shaders.dmsv in 18 s; GPU 8-24 ms/frame, zero FATAL/CRASH
- screenshot shows the game's opening scene (not black): 93.8% mid-tones, 4.1% dark
- TEMP [dbg] lines were not reached (rate-limited counters exhausted before the boot completed cleanly)

Evidence: dm/ohos/out/e2e_markers_2026-11-15.txt
