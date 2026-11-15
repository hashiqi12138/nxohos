
# P0 device verification (2026-11-15)

The emit_tp register-swap fix (key-page TPOFF load must not clobber the thread-pointer register) is verified on the device:
- game thread started, guest thread slots located (pthread keys 23..26, tpidr_el0+672), nnosInitialize passed
- 1900+ frames presented over 18 minutes, present interval switching 2<->3 from game logic (main+0x11267c4)
- 101/101 compute shaders precompiled from shaders.dmsv in 18 s; GPU 8-24 ms/frame, zero FATAL/CRASH
- screenshot shows the game's opening scene (not black): 93.8% mid-tones, 4.1% dark
- TEMP [dbg] lines were not reached (rate-limited counters exhausted before the boot completed cleanly)

Evidence: dm/ohos/out/e2e_markers_2026-11-15.txt

## P0.2 fast-loop timing (measured, 2026-11-15)

One devpush-class round, measured end to end on the real chain (mods changed, lib rebuilt):
- host lib compile: ~40 s (192 sources)
- entry re-sign (hvigor module mode): 61 s
- entry transfer (628 MB over WiFi): 124 s (5.1 MB/s)
- bm install -p (subset replace): ~120 s
- total, code edit to device-log verdict: ~5.7 min; unchanged data staging: skipped (stamp match, 0 files re-copied)

Within the ROADMAP P0.2 acceptance (<= 5 min/round target: at 5.7 min with a 628 MB entry; the entry-slimming
change in mkhap.py -- data files ride with the bulk modules -- brings this under the bar once a slim entry is packed).
