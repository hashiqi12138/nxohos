# P3.5: Measuring the Shrine scene, and the assumptions the measurement killed

Updated 2026-11-18. This supersedes the numbers in P3.4 wherever the two disagree; P3.4's own §"corrections" section
already retracts its first set of conclusions, and this document retracts two more.

The reason this document exists is that P3.4's numbers were not comparable to each other. Every counter in this
port is cumulative since start-up, so a per-frame figure is only meaningful when both samples come from the same
window of the same scene. P3.4 subtracted counters taken minutes apart, across a title screen, a load and gameplay,
and reported the result as a frame cost. Several conclusions followed from that and were wrong.

## The measurement tool

`dm/ohos/measure.py` takes the two samples itself and divides by the frame count between them:

```
python dm/ohos/measure.py --scene shrine --samples 3 --gap 20
```

It refuses a pair whose counter did not advance by the requested gap rather than averaging a bad sample in, and it
waits for the counter to move instead of sleeping a guessed interval. Fields are per frame: `frame_ms`, `gpu_ms`,
`replay_ms`, `sets_ms`, `collect_ms`, `hash_ms`, `allocw_ms`, `new_sets`, `reused_sets`, and (since the copy-back
counters were added) `copy-back ms a submission` from the pool line.

Scene stability is visible in the output: when `frame_ms` and `gpu_ms` come back identical across all three pairs,
the scene is holding still and the numbers can be compared.

## Baseline: Shrine of Resurrection, gameplay

Build 2000786080, code at cbce15b9 (upstream db07cbde merged), no experimental change applied. Game entered through
Continue into the existing save; screenshots confirmed the shrine interior rather than a menu.

Three pairs, 20 frames apart, medians:

| Metric | Value | Share of the frame |
|---|---:|---|
| Frame | 132.6 ms | 100% |
| GPU timestamp span | 82.5 ms | 62% |
| CPU replay | 77.5 ms | 58% |
| descriptor sets (whole `stage_set`) | 40.9 ms | 31% |
| ├ alloc + update | 16.0 ms | 12% |
| ├ collect | 8.5 ms | 6.4% |
| └ hash | 0.65 ms | 0.5% |
| new sets per frame | 1158 | |
| reused sets per frame | 871 | 43% reuse |

`frame_ms` and `gpu_ms` were identical in all three pairs (132.58 / 82.46), so the scene was stable throughout.

Two things follow that are worth stating plainly. GPU span and CPU replay each take more than half the frame, so
they overlap: the frame is not the sum of the two. And the three measured parts of `stage_set` add to 25.2 ms, not
40.9 ms, so roughly 15.7 ms of it is the memo lookup, whose counter is not present in this build.

## Two assumptions this measurement killed

### Copy-back is not the bottleneck

`completion:` lines showed `pool copy-back 23.8 ms` and `32.4 ms`, which looked like the largest single cost after
the replay. They were not averages. That log line only prints when a completion takes 50 ms or more and only for
the first thirty occurrences, so it is a record of the worst cases, not of typical ones.

Adding a cumulative counter (and shortening the pool report from 600 submissions to 100 so it appears at 7 fps)
gives the real figure:

```
copied pools: 342.2 MB synced in the last 100 submissions (3.42 MB each); copy-back 1.36 ms a submission
copied pools: 417.2 MB synced in the last 100 submissions (4.17 MB each); copy-back 2.73 ms a submission
copied pools: 392.6 MB synced in the last 100 submissions (3.93 MB each); copy-back 3.68 ms a submission
```

1.4-3.7 ms per frame, moving 3.4-4.2 MB. Against a 132.6 ms frame that is noise. The earlier reading was a sampling
artifact, and acting on it would have meant optimizing 3 ms.

### Growing the descriptor cache does not help

P3.4 recorded 309,764 misses of which 94.4% asked for content the submission had already built, and concluded the
8192-slot direct-mapped table was too small. Growing it to 65,536 was measured: misses rose to 786,694 and the
frame rate did not improve.

The reason is in the same measurement. `slot empty/stale` was 96% of misses, and that is `g_set_epoch` advancing on
every submission, which empties the table regardless of how many slots it has. A bigger table is emptied just as
thoroughly. The 94.4% figure describes content repeated *within* one submission, which the table does hold; what it
cannot do is carry anything across a submission boundary, and that is a lifetime question, not a capacity one.

Cross-submission reuse is not a free change: `slot_recycle()` calls `vkResetDescriptorPool`, which invalidates the
sets the cache would hand back, and the referenced buffers and views are freed with the slot. Hash equality and an
age limit do not preserve that.

## Where the frame time actually is

GPU span 82.5 ms against a pass total of 52.2 ms leaves about 30 ms in which the GPU had no recorded work. That gap
is the shape of the pipeline, not a slow pass: `mtl_sub_begin` waits on the previous submission's fence
(`mtl_wait(g_fence, ~0ull)`) before syncing the copied pools, so the replay of one frame does not overlap the GPU
work of the last. The wait exists for correctness - the pool sync must follow the previous copy-back - so removing
it is a lifetime change, not a tuning knob.

That makes the two candidate directions, in order of expected value:

1. **Overlap the pipeline.** Recover the ~30 ms of GPU idle. Highest value, highest risk: it touches the same pool
   sync ordering that the copy-back correctness depends on.
2. **Shrink `stage_set`.** 40.9 ms, of which alloc+update is 16 ms and the memo lookup is the unmeasured ~15.7 ms.
   Bounded by the pipeline: if the GPU is waiting on the CPU, shortening the CPU work does help, but the 43% reuse
   rate says most of that work is building sets the frame will not ask for again.

## Reproducing

```
# build and install
python dm/ohos/build_ohos.py --lib --game 01007EF00011E000
DM_VERSION_CODE=<higher than the device's> python dm/ohos/mkhap.py --game 01007EF00011E000 \
    --lib dm/ohos/out/01007EF00011E000/libdmhost.so --out <dir>
hdc shell aa force-stop dev.eden_emu.ohos
hdc file send <dir>/dmhost-signed.hap /data/local/tmp/nx2a-e/entry-default-signed.hap
hdc shell bm install -p /data/local/tmp/nx2a-e -r
hdc shell aa start -b dev.eden_emu.ohos -a EntryAbility

# enter the shrine: library card at 968,739; virtual A at 2446,882 a few times, ~5 s apart
# then measure
python dm/ohos/measure.py --scene shrine --samples 3 --gap 20
```

Environment for the counters (`games/01007EF00011E000/env.txt`, pushed with `hdc file send` because a shell
redirect is denied):

```
DM_VK_PROF_MS=20
DM_VK_PROF_SLOW=1
DM_VK_PASS_TIMES=1
```

## What this document does not claim

No performance improvement. Every experimental change tried against this baseline was either measured as neutral
or reverted; none is in the tree. The value here is the measurement method and the two assumptions it disproved.
