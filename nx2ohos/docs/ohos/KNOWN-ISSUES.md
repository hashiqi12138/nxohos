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
