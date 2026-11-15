# Known issues

Real findings from the port's history, each with its signature, cause and fix or status.
New crash signatures get triaged here the same day they appear (roadmap P1.5's standing
rule). Related: [DEVICE.md](DEVICE.md), [BUILDING.md](BUILDING.md), [REPOS.md](REPOS.md).

## (a) "the output comes from an old rewriter" — loader refuses the mods

* **Signature** (device, at module load):
  `the output comes from an old rewriter; rewrite the modules again with dm/tools/patch_nso.py`
  — or, for a module rewritten for the wrong target:
  `the module was rewritten for apple, this host runs on ohos: rewrite it with DM_TARGET=ohos dm/tools/patch_nso.py`.
* **Cause**: `dm/host/loader.c` reads a metadata line the rewriter writes per module; a
  module rewritten before that line existed (or for another target) fails the scan, and the
  host dies rather than run stale binaries.
* **Fix**: always rewrite the modules with the **current tree**, with
  **`DM_TARGET=ohos`** set, and pack them fresh (`porter port --platform hap` or
  `patch_nso.py` directly). A mods directory older than the host build is not a valid pack.
* **Status**: closed (the guard in the loader is the fix; the failure mode is loud).

## (b) `emit_tp` register-swap bug class (thread-pointer clobber)

* **Signature**: a crash on the key-page load path — the emitted `emit_tp` sequence loaded
  the TPOFF into the same register that held the thread pointer, clobbering
  `tpidr_el0`-derived state; key-page reads then dereferenced garbage.
* **Cause**: on OHOS the rewriter emits slot accesses as `tpidr_el0 + TPOFF` (musl owns the
  thread pointer; see the `DM_TARGET` block in `patch_nso.py`), and one emit path passed
  the same register for both the thread pointer and the scratch.
* **Fix**: `emit_tp` now asserts distinct registers (`emit_tp needs distinct registers on
  ohos`) and the key-page load sequences were corrected. **Device re-verification is
  pending** — the fix was committed after the last on-device run (roadmap P0.1 is exactly
  this re-verification).
* **Status**: fixed in the tree; **open until P0.1 passes on device**.

## (c) Truncated signed HAP from a killed mid-queue mkhap run

* **Signature**: a full-split build killed part-way through the per-module `assembleHap`
  queue leaves a partial signed output in the results folder. Observed: **data1 came out
  2.08 GB instead of 3.67 GB**; the follow-up install of that set failed with
  **"no signature file"** (hdc/bm reading a zip whose tail never got written).
* **Cause**: mkhap hardlinks/copies whatever `*-signed.hap` files the interrupted hvigor
  run happened to leave; nothing checks completeness before `place()`.
* **Fix (manual, works today)**: delete the partial signed outputs (the
  `dmhost-haps/` folder, and the `ohos_build_*` tree's stale module outputs) and re-run
  `mkhap.py --game <id> --data <pack>`. Resume-script pattern, since mkhap rebuilds every
  module anyway: guard the run (`--check-patches` first), confirm free disk
  (`2 × pack + 512 MB`), and after the run verify every `dataN` HAP's size against the
  manifest's per-module byte totals before installing.

  ```powershell
  # before re-running a killed full build:
  Remove-Item -Recurse -Force <out>\dmhost-haps            # partial set, "no signature file"
  Remove-Item -Recurse -Force F:\ohos_build_dmhost_<game>  # stale per-module outputs
  python nxnative/dm/ohos/mkhap.py --game <id> --data <pack>   # full re-run
  ```
* **Status**: known operational hazard; mkhap's size/disk checks catch the disk-full case
  but not the killed-mid-queue case (the manifest's per-module sizes printed by mkhap are
  the cross-check).

## (d) stderr is a black hole in an OHOS app

* **Signature**: nothing from `fprintf(stderr, …)` ever shows up on device, crash reports
  included — an app process's stderr is not readable.
* **Fix**: `dm/host/crash.c` now routes its whole report through a line-buffered shim to
  `dm_ohos_logv` (`dm/ohos/ohos_log.c`), which logs with **hilog** domain `0x0201`, tag
  `dmhost` (`OH_LOG_INFO`, async-safe enough for the handler). The host's `LOG()` goes the
  same way; `fprintf`/`fputc` on `stderr` inside `crash.c` are `#define`-redirected.
* **Consequence**: read crash reports with `hilog -x -T dmhost` (see [DEVICE.md](DEVICE.md#reading-hilog));
  the crash lines look exactly as they do on the Mac (`[dm] CRASH sig=…` + registers +
  backtrace). Note the hilog domain must stay inside `0x0000–0xFFFF` or lines are silently
  dropped (a 0xD0021-style tag was accepted by `OH_LOG_Print` but never reached the buffer).

## (e) Leaked `ohos_*` temp dirs once filled C:, and bsdtar's misleading errors

* **Signature**: repeated guard/export runs leaked one full extracted upstream tree each
  (`ohos_export_*` / `ohos_guard_*` under `%TEMP%`) until C: ran out; afterwards bsdtar
  failed every extraction with a **misleading "No such file or directory"** under its
  `\\?\`-prefixed temp paths — the real cause was a full disk.
* **Fix**: `export_ohos_patches.py` and `test_ohos_patches.py` now extract upstream with
  **Python's `tarfile`** (from `git archive` into a real temp file), and the temp dir is
  **always cleaned up** (try/except around the extraction, `shutil.rmtree` on exit).
* **Rule**: if a tool suddenly reports not-found errors on paths that exist, check disk
  space first.

## (f) Windows rewriter quirks: `DM_REWRITE_J=1` and `PYTHONUTF8=1`

* **`DM_REWRITE_J=1`**: the parallel rewrite pool uses `signal.pthread_sigmask` around its
  fork-based workers; **Windows has neither `pthread_sigmask` nor `fork`**, so the scan
  runs serially there — set `DM_REWRITE_J=1` to make that explicit (the env var is
  validated: it must be a positive integer, `[RED]` otherwise). The rewriter still works
  without it; the setting avoids the misleading expectation of parallelism.
* **`PYTHONUTF8=1`**: the rewriter's output includes non-ASCII text (e.g. the U+2212 minus
  sign `−` in cut reasons); on a cp936 console Python's default encoding dies on it. Set
  `PYTHONUTF8=1` in every shell (the roadmapped rule: Windows host quirks are documented,
  not worked around silently).
* Also verified on the same topic: the rewriter refuses Capstone 6 (`capstone 5.x`
  required, `[RED] Capstone … is unsupported` at import).

## (g) The app can be silently replaced by another sharing `dev.eden_emu.ohos`

* **Signature**: an install reports success but the on-device app is not this build — its
  `versionCode` is not the HAP's (`bm dump -n dev.eden_emu.ohos`), or the crash you are
  chasing looks exactly unchanged.
* **Cause**: DevEco's automatic signing material is bound to the bundleName
  **`dev.eden_emu.ohos`** (`dm/ohos/hap/AppScope/app.json5` documents the binding: a HAP
  whose app.json5 does not match the profile fails SignHap). Any other app signed with the
  same profile installs over this one — and an install with an equal-or-lower versionCode
  is refused as a downgrade (`install version downgrade`), which can hide the replacement.
* **Fix**: reinstall with a **higher versionCode** (`DM_VERSION_CODE` / `--version-code`,
  or let mkhap/devpush use Unix seconds), then verify on device:
  `bm dump -n dev.eden_emu.ohos` → `versionCode` equals the HAP's, and
  `pidof dev.eden_emu.ohos` is the process whose log you read. Prove the device really took
  *this* HAP before drawing conclusions from a "reproduced" crash.
* **Status**: standing operational rule; a dedicated bundleName would need a new DevEco
  signing profile for it (documented in app.json5's comment).

---

### Triage discipline (from the roadmap's standing tasks)

* Every new crash signature is classified into this file the same day it appears
  (signature → cause → fix or status), before the next sync lands.
* TEMP diagnostic logs die with the fix that made them obsolete.
* Any new Windows-host quirk goes here (f), not into folklore.

## (h) [RED] unknown record op 59 (Breath of the Wild, 2026-11-15)

**Signature**: [dm] [RED] [vk] unknown record op 59
**Source**: dm/gfx/vk/gfx_vk_exec.c, the replay switch's default (VKRED_ONCE 82).
**Triage**: op 59 is OP_PATCH_SIZE (the OP_ table in dm/gfx/nvn/nvn_obj.h; recorded by 
vnCommandBufferSetPatchSize).
BOTW sets the tessellation patch size once at boot. The Vulkan replay stage has no tessellation (the Metal side skips
tessellated draws too), so the op is recorded and ignored: a case OP_PATCH_SIZE: break; in the switch keeps it out of
the unknown-op default. No draw content is affected while the game draws no tessellated geometry.
**Fix**: the no-op case, marked with an OHOS comment (the patch exporter claims the file through it). Status: fixed.

## (i) THREAD_BLOCK_6S: the system kills the app during a long unpack (2026-11-15)

**Signature**: the process disappears with no crash signal in `hilog -T dmhost`; `hisysevent` has
`foundation/AppMS: [AMSI6516] ... PROCESS_KILL, msg=THREAD_BLOCK_6S, reason=THREAD_BLOCK_6S, FOREGROUND=1`.
**Source**: any long synchronous loop on the ArkTS thread — here the 41,106-file `.nxpk` unpack.
**Triage**: HarmonyOS kills a foreground app whose thread blocks for 6 s. It looked like a random crash at
three different points (180 MB, 5 GB, 6 GB) because each yield policy left stretches that were still too long.
`setTimeout(0)` is not enough: ArkTS drains microtasks in the same turn.
**Fix**: yield every 1 MB of data **and** every 5 files with `setTimeout(1)`. The byte-based check matters —
one 500 MB asset would otherwise hold the thread for seconds. Verified: 41,106 files, no kill.
**Rule for any future long loop in ArkTS**: keep each synchronous stretch well under a second; measure with the
byte count, not just the iteration count. Status: fixed.

## (j) lseek does not hold across many reads; ReadOptions.offset does (2026-11-15)

**Signature**: a 0-byte file named `NXPK` appears in the sandbox; the tar reader parses the container header.
**Source**: `fs.lseek(fd, 80, SEEK_SET)` once, then `readSync(fd, buf, {offset: 0, length: 512})` in a loop.
**Triage**: `ReadOptions.offset` is the **file position**, not a buffer offset. After the first read the
descriptor had advanced past the container header and the loop never re-established the intended position.
**Fix**: track the position in the loop and pass it on every `readSync`; drop `lseek` entirely. Status: fixed.

## (k) HTTP streaming on the device: three separate limits (2026-11-15)

A 14.75 GB pull over `http.requestInStream` failed in three different ways, each with its own signature:

| Signature | Cause | Fix |
|---|---|---|
| `code 2300023 Failed to write the received data to the disk` at ~52 MB | `expectDataType: ARRAY_BUFFER` buffers the whole body in memory | `requestInStream` + `dataReceive`, write each chunk to the descriptor |
| `code 2300028 Operation timeout` with `dlSz == Content-Length` | `readTimeout` is the **whole request's** budget; one hour expired after every byte arrived | six hours, and a timeout no longer discards what arrived |
| connection `Established`, no bytes for 17 minutes, no error | TCP half-open after a WiFi drop; the six-hour budget would have waited that long | 60 s no-data watchdog destroys the request, `.part` keeps the bytes |

**Rule**: for a multi-GB download, set the timeout from the measured rate with headroom, keep a partial file,
and watch for progress rather than trusting the socket state. Status: fixed.

## (l) ArkUI responsiveness traps found while building the library (2026-11-16)

Five separate defects, each of which looked like a different bug. All share one root: **ArkUI's reactivity
boundaries are not where a plain TypeScript reading suggests.**

| # | Symptom | Cause | Fix |
|---|---|---|---|
| 1 | The canvas followed `padOpacity` but the label stayed at 100% | the field was `private`, not `@State`; a plain field never triggers a rebuild | `@State` |
| 2 | The storage rows stayed at `计算中…` although the log printed the number | a `@Builder`'s parameters are captured **when it is called**, so a value passed in is frozen | write the row inline, or read the state inside the builder |
| 3 | Stepper buttons in a panel over the game did nothing | the overlay's `onTouch` on the parent `Stack` took the touch before the panel's children saw it | `hitTestBehavior(HitTestMode.Transparent)` in edit mode |
| 4 | The detail sheet could not be dismissed | a transparent `Column` wrapping `Blank() + Scroll()` filled the screen above the scrim and swallowed the taps meant for it | make the sheet a `Stack({alignContent: Bottom})`; size the `Scroll` by its content |
| 5 | A long-press gesture never fired in a test | `uitest uiInput swipe` is a drag, not a hold | `uitest uiInput longClick` |

**Rules**:
- A field that drives a label must be `@State`; `drawPad()` reading it is not enough.
- A `@Builder` is not reactive in its parameters - pass state, not values.
- **A transparent container still intercepts touches.** `Blank()` is not empty space, it is a full-size hit target.
- A sheet must always render an explicit close control; the scrim alone is not one when the content fills the screen.
- `uitest` has `click`, `doubleClick` and `longClick`; `swipe` is a drag and cannot stand in for a hold.

Status: all five fixed.

## (m) Walking 41k files on the UI thread force-exits the app (2026-11-16)

Opening the settings page killed the process about three seconds in:

```
THREAD_BLOCK_3S   faultType: 3
APP_INPUT_BLOCK   faultData.forceExit:1
```

`treeSize()` called `fs.statSync` on every entry of a 41,106-file tree in one synchronous run. This is the same
watchdog that killed the P2.1 import (issue i), with a shorter deadline: **3 s for input blocking, 6 s for a
thread block.**

Two further traps came out of the fix:

- The first yielding walk still never finished: it kept a bare stack of directories, so a yield in the middle of a
  listing lost the cursor and re-read the same directory for ever. Each directory needs its own frame with an
  index into its listing.
- `setTimeout(cb, 0)` does **not** move work off the UI thread - it only defers it. Yielding in slices is what keeps
  the watchdog fed; there is no worker-thread escape here.

**Rule**: any walk over thousands of files yields every few hundred entries, and keeps its place while doing so.
Measured after the fix: 15.8 GB summed in about 1 s with no watchdog trip.

Status: fixed.

## (n) The save directory was shared by every game (2026-11-16)

`DM_SAVE` was one directory for the whole app. A second game's save would have overwritten the first's: the guest
mounts whatever `DM_SAVE` names and knows nothing about titles.

P2.1 left this behind because it was verified with a single game installed, where one shared directory and one
per-title directory look identical.

**Fix**: `DM_SAVE` is `<save>/<title id>`, taken from the root's last path component (`files/games/<title>`), with
`default` as the fallback.

**Rule**: any per-game state under the sandbox needs the title in its path, and a single-game test cannot prove it.

Status: fixed.

## (o) Native changes need a `--lib` rebuild, not just a HAP rebuild (2026-11-16)

The OHOS port compiles from the source tree (`build_ohos.py`: `PORT = srcs('ohos/*.c')`), so editing a `.c` under
`dm/ohos/` does nothing to the installed app until `libdmhost.so` is rebuilt:

```
python dm/ohos/build_ohos.py --lib --game <title>          # 6 s for one file
copy dm/ohos/out/<title>/libdmhost.so  <build-tree>/entry/libs/arm64-v8a/
hvigorw --mode module -p module=entry@default assembleHap
```

Measured: 6 s for a one-file change, which is cheap enough to keep the loop tight. Skipping the copy silently ships
the old native code while the ArkTS side looks correct - which is exactly how a fixed bug appears to still be there.

Status: not a bug, a required step.
