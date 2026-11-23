# P3.4: BOTW gameplay performance investigation

Updated 2026-11-17. This revision replaces the earlier interpretation of cumulative counters and invalid cross-submission descriptor reuse experiments.

## Scope and evidence

Device: HBN-AL80, Maleoon 910. Installed versionCode: 2000786000. Title: 01007EF00011E000. Screenshots verified Link standing inside the Shrine of Resurrection; menu and loading samples are excluded from gameplay conclusions. No runtime code was changed for this measurement.

Raw evidence in the workspace: `.screenshots/botw-current.log`, `botw-current.png`, `botw-pass-final.log`, and `botw-pass-world2.png`.

## Baseline, before pass timestamps

The captured log contains 677 slow-replay samples. These are threshold-selected samples, not an unconditional census of every frame. Adjacent cumulative descriptor counters span 676 presents.

| Metric | Result |
|---|---:|
| Replay p50 / p95 | 74.9 / 87.1 ms |
| Descriptor path p50 / p95 | 41.8 / 49.6 ms |
| Command-buffer cuts p50 / p95 | 9.9 / 17.3 ms |
| New sets per present, counter delta | 1163.494 |
| Content-cache hits per present, counter delta | 878.028 |
| Descriptor collection per present | 8.847 ms |
| Descriptor hashing per present | 0.732 ms |
| Allocation and descriptor updates per present | 17.980 ms |

The larger descriptor-path timer includes work outside those three counters, including memo checks and binding-related work. Its total must not be interpreted as allocation alone. Memo fast-path hits are counted separately from content-cache hits.

Three 300-frame reports in the same gameplay period show frame-time p50 of 149.64, 150.55 and 154.47 ms; GPU timestamp-span p50 of 89.03, 89.97 and 93.17 ms. The screenshot shows approximately 7 fps.

## Existing GPU pass timing experiment

Enabled `DM_VK_PASS_TIMES=1` in the game's environment, restarted the application, selected Continue and the existing Shrine of Resurrection save, then verified gameplay by screenshot. Existing executable and shaders were retained.

At frame 3000, the all-gameplay 300-frame report gives:

- Frame time p50 / p95: 158.92 / 180.57 ms.
- Submit plus present p50 / p95: 103.85 / 122.94 ms.
- GPU timestamp span p50 / p95: 94.35 / 105.28 ms.
- Pass report: 128 recorded pass positions, aggregate mean durations 52.82 ms per frame.
- Most expensive recorded pass: position 21, 1600x900, format 37, mean 4.36 ms, last metadata 1998 draws.

At frame 3300, frame p50 is 162.55 ms and GPU span p50 is 96.21 ms. Instrumented and baseline runs are distinct windows; these figures do not establish an instrumentation slowdown of a particular amount.

### Measurement limitations

`gfx_vk_core.c` caps pass timestamps at PASSQ=128 per submission. The report reaches that cap. Remaining passes, transfer/barrier work and possible gaps between early submissions are not separated. The 52.82 ms report is a sum of per-position means; the 94.35 ms figure is a median over frames, so their difference is not an exact idle-time measurement.

The overall GPU timer starts in the first main command buffer and ends in the final one. With `DM_VK_SUBMIT_SPLIT` enabled by default, early parts are sent before recording of later parts finishes. Its interval may therefore include GPU starvation while waiting for CPU submission. TOP/BOTTOM timestamp intervals are not GPU utilization counters.

Pass metadata retains the last observed dimensions and draw count while durations are averaged by position. Draw counts and pass identities must be checked in a stable scene before mapping a reported index to a specific effect. The draw counter increments on the replay draw path; 1998 describes that counter, not a count inferred from set allocation.

## Corrections to the previous report

1. Hundreds of thousands of new sets were cumulative since startup, not per-frame counts. Dividing draws at present 1200 by sets at present 1762 mixed different windows.
2. The old miss classifier measured slot state. `slot expired/free` does not establish unique descriptor content. Its touched-slot count duplicated the epoch-mismatch condition and was not a distinct-content count.
3. Occupied slots with another layout are also mapping conflicts. Neither those counts nor the 0.3% same-layout hash-mismatch figure establish how many misses are recoverable by associativity. An epoch-local seen-key shadow table is needed.
4. The previously quoted 4.76 ms GPU result came from a different scene/window. It establishes neither gameplay GPU cost nor a causal OpenGTX improvement.
5. `g_ord_parts < 15` caps ordinary command-buffer cuts; the default draw threshold is `g_x_cbdraws=128`. Raising 15 permits more cuts, rather than reducing their frequency.
6. The profiler threshold defaults to 40 ms. `DM_VK_PROF_MS=20` selects slow replays above 20 ms; it does not guarantee every frame is captured.
7. The old cross-frame experiments removed epoch validity checks while using descriptor sets allocated from recyclable submission pools. `slot_recycle()` resets those pools and frees deferred buffers/views. Reset invalidates allocated sets. Hash equality and a short age limit do not preserve resource lifetime. Both experiments were rolled back; their 1 fps results do not prove a cache-locality regression.
8. Equal buffer handle/offset/range describes equal binding metadata, not equal buffer bytes. Correct descriptor reuse requires live sets and live referenced resources, plus collision handling and synchronization.
9. The Darwin-style `demoted` diagnostic is not evidence of HarmonyOS QoS throttling.

## Current conclusion and next measurements

CPU replay is expensive, with descriptor processing its largest measured category. GPU pass intervals also account for substantial elapsed time. The available data does not yet separate all GPU work from early-submission gaps, nor justify declaring the workload exclusively CPU- or GPU-bound.

Next steps:

1. Increase diagnostic timestamp coverage and report truncation explicitly, or timestamp each command-buffer part independently. Record same-frame spans rather than subtracting aggregate percentiles.
2. Separate allocation, per-write descriptor update and bind/memo CPU costs, including call counts.
3. Use an epoch-local shadow seen-key table to distinguish first-seen binding content from evicted revisits, while preserving existing lifetime checks.
4. Compare one reversible change at a time in the same save/camera after warmup. Preserve the Maleoon per-write descriptor-update workaround until a correctness test supports changing it.

No performance improvement is claimed by this report. No commit or push was requested for this revision.
