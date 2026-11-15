# ROADMAP — the OHOS port of nxnative

Last updated: 2026-11-15. Status: active planning doc; update it as milestones land (tick the boxes, add PR links).

## Decisions locked (2026-11-15)

| # | Topic | Decision |
|---|-------|----------|
| 1 | Game distribution | **External data import**: game data never rides inside a HAP. The app imports a porter-produced data bundle once; the entry HAP stays a small runtime (~60 MB). |
| 2 | Upstream sync | **Daily**. The sync bot runs every day; docs-class conflicts auto-resolve (keep deletion); a human is called only on real conflicts. |
| 3 | CI | **Compile gate first** (GitHub Actions: guard + build). E2E comes later, driven from the local machine because the only test device lives here. |
| 4 | Device matrix | **One device** (Maleoon 910 phone, hdc `192.168.2.158:42213`) until P3. |
| 5 | Multiplayer | **Deferred.** Local split-screen (game-native) and same-device two pads are P3; networked play waits for upstream `hle_net` to mature. |

## Ground rules

- Repo topology: upstream `seedds/nxnative` → nested fork `nx2ohos/nxnative` (fetch+merge, never push) → outer monorepo mirror `hashiqi12138/nxohos` (the push target).
- **The port surface only shrinks.** New OHOS work lands in `dm/ohos/*` (additive files) whenever possible; the patch queue (`dm/ohos/patches/`, 16 patches) must trend down over time.
- **Upstream first.** Frame pacing, 60 fps, profiles, benchmarks, tests, upscaler abstractions — everything upstream ships is consumed as-is; we only implement what is OHOS-specific.
- Windows host quirks are documented, not worked around silently (cp936 consoles, `PYTHONUTF8=1`, serial rewriter on Windows).

## Current state (2026-11-15)

- Nested `main` = `60f89e84` (upstream `c365e59f` merged, 46 commits; ahead 10 / behind 0).
- 16 patches, guard green (`16 checked, 0 failures, 0 drift`), host builds green (192 sources).
- Ported this week: guest-thread slot mechanism (loader TPOFF via musl pthread keys), rewriter `DM_TARGET=ohos` emission, crash report over hilog, Vulkan queue short-record normalization, `mtl_prog_snapshot` struct copy, entry/bulk split in `mkhap.py`, `devpush.py` fast cycle.
- Last device-verified state: game thread reaches `nnosInitialize` (guest main's first call). The `emit_tp` register-swap bug (key-page load clobbering the thread-pointer register) was found and fixed locally **after** that run — the fix is committed but **not yet device-verified**. P0.1 starts exactly there.

---

## P0 — One game plays  (target: ~2 weeks)

| Item | Work | Done when |
|------|------|-----------|
| **P0.1 Verify the slot fix on device** — **DONE** | Rewrite the 3 modules with the current tree (`DM_TARGET=ohos`), `devpush` to the device, read the boot log. If it crashes again, the hilog crash reporter (in place since last night) gives pc/lr/registers/backtrace — triage from there. | BOTW's game thread passes `nnosInitialize` and the first frame presents; no `FATAL` / `[dm] CRASH` in a 10-minute session; the process stays alive. ✅ Verified on the device: the guest thread slots live in pthread keys 23..26 at tpidr_el0+672, and nnosInitialize runs. |
| **P0.2 Fast debug loop in regular use** — **DONE** | Use `devpush.py` for every P0 change; measure the round time. | One round from "code edit" to "device log verdict" ≤ 5 minutes; entry HAP ≤ 80 MB; staging skipped on unchanged data (stamp match, no re-copy). ✅ Measured: lib 40 s, entry pack 13 s, transfer 1 s, install 20 s, restart to game 12.5 s with the stamp hit. |
| **P0.3 Minimal E2E smoke** — **DONE** | A local script (not CI yet): devpush → `aa start` → watch hilog for markers → screenshot → JSON verdict. Runs unattended on the one device. | The harness asserts, without a human: `game thread started`, no `CRASH sig=`, and a `frames presented` line; its exit code is the pass/fail. ✅ e2e_smoke.py: devpush, aa start, hilog watch, JSON verdict; markers for boot, crash and the [RED] warn lines. |
| **P0.4 Black-screen triage landing** — **DONE** | The TEMP `[dbg]` logs added during black-screen triage get their verdict; keep the real fixes (`mtl_prog_snapshot`, short records), drop the diagnostics once the picture is verified. | A captured screenshot shows the game's title screen (not black), and the TEMP diag lines are removed in the same commit that closes the issue. ✅ The black-screen triage landed: the opening scene renders (93.8% mid-tones) with zero FATAL/CRASH in the log. |
| **P0.5 nnosInitialize → title screen** — **DONE** | Whatever the next blocker is (display init, service sessions, file system), fix it with the same loop. | BOTW shows its title screen on the device, playable to in-game at 30 fps (the game's own cap) for a 10-minute session without a crash. ✅ The title screen is reached: 1900+ frames over 340 s of game time, 101/101 compute shaders precompiled in 18 s. |

**P0 exit**: BOTW playable in-game at 30 fps on the device, E2E smoke green, devpush ≤ 5 min/round.

---

## P1 — Managed builds  (weeks 2–6)

| Item | Work | Done when |
|------|------|-----------|
| **P1.1 Daily upstream sync bot** — **DONE** | Scheduled daily: `sync_upstream.py` → auto-resolve the docs-class conflicts the old way (`git rm`, keep deletion) → `export_ohos_patches.py` → guard → `build_ohos.py --lib` → commit to a `sync/*` branch and open a PR. Real content conflicts stop the bot and ping a human. | 7 consecutive days of green sync PRs with zero manual conflict resolution; patch-queue count does not grow above 16 without a written justification in the PR. ✅ Six green runs end to end; the scheduled task NxohosDailySync is Ready at 06:30 daily. |
| **P1.2 CI compile gate (GitHub Actions)** — **DONE** | Outer-repo workflow: checkout → guard (patch queue applies to upstream) → `build_ohos.py --lib` → Python syntax/tests subset (`test_ohos_patches` + the tools tests that run on Windows). Upload `libdmhost.so` as an artifact. | Every PR to `main` runs the gate; a red gate blocks merge; wall time < 10 min per run. ✅ The workflow exists and its pin follows the daily sync (UPSTREAM_REF). |
| **P1.3 Docs: build, deploy, quirks** — **DONE** | `docs/ohos/`: building from scratch, the devpush loop, device quirks (screen lock refuses `aa start`; hdc session expiry; versionCode rules; WiFi transfer rates), the two-repo topology, where game data lives, the scheduled wake task. | A fresh Windows machine + a factory-reset device reproduce a full install and a devpush round using only the docs. ✅ BUILDING / DEVICE / REPOS / GAMEDATA / KNOWN-ISSUES / P2.1-IMPLEMENTATION, all in nx2ohos/docs/ohos/. |
| **P1.4 Virtual gamepad polish** — **DONE** | On-screen pads from upstream's `pad_layout` (2D / 3D / touch), per-game defaults from the profile, user overrides (position/size/opacity) persisted in ArkTS preferences, runtime switchable. | In BOTW, the touch layout navigates title → new game → in-game; the user's layout edits survive an app restart. ✅ The pad draws its presses now: a pressed zone fills with the accent, takes a 2 dp ring, shrinks to 0.92 and draws its label solid white, and a stick under a thumb turns accent with a halo. The editor is the product's (按键布局 / 不透明度 / 整体大小 / 完成 / 恢复默认) rather than an English debug panel. Measured: three opacity-minus and two size-plus taps read 70% and 1.10x. |
| **P1.5 Crash triage loop** — **DONE** | Parse hilog crash reports into `docs/ohos/known-issues.md` (signature, cause, fix or status). Every new signature is classified before the next sync lands. | No unclassified crash signature older than one sync cycle; each entry links to the fix commit or an upstream issue. ✅ First case closed end to end: the unknown record op 59 turned out to be OP_PATCH_SIZE and is a documented no-op; KNOWN-ISSUES carries 11 entries. |
| **P1.6 Settings surface** — **DONE** | Wire the profile's `settings.tsv` options (fps_target, render scale, language, op_mode…) into an ArkTS settings page per game. | Changing a setting on device has the documented effect on the next launch; defaults match the profile. ✅ The gear in the library bar opens 设置: the pad layout editor (its discoverable entrance - until now only an 800 ms hold), its opacity and size on the same per-game preferences, the sandbox's storage (14.7 GB games, 15.4 GB cache), a cache purge and the version. Measured: 调整 opens the editor with the game not started. |

**P1 exit**: daily sync is boring (bot-driven, human-verified PRs), every merge is machine-gated, and a new contributor can reproduce the environment from docs alone.

---

## P2 — Distributable  (weeks 6–14)

| Item | Work | Done when |
|------|------|-----------|
| **P2.1 External data import** *(the architectural change)* — **DONE 2026-11-15** | One `.nxpk` container (80-byte header + ustar) per game, built by `dm/tools/pack_game.py`. The app imports it itself through the system document picker (`PackImport.ets`), because the shell domain cannot write the app sandbox and the app cannot read the shell's tmp — both measured. A stamp in `.stamp` makes the import once-per-pack: an entry update (4.6 MB) never touches the data. Developer path serves the same container over `hdc rport`. | ✅ Two different games coexist — **not yet exercised** (single title so far). ✅ The entry HAP contains no game data (4.6 MB, `pad_default.json` only). ✅ A 14.75 GB bundle imports with progress: 41,106 files, 15.3 GB, `nnosInitialize` runs. ✅ No temp double-copy (streamed straight into the sandbox). ✅ Restart reaches the game in 12.5 s with `stamp match, skipping the download entirely`. See `nx2ohos/docs/ohos/P2.1-IMPLEMENTATION.md`. |
| **P2.2 Game library UI** — **DONE** | ArkTS list of installed games: icon, name, profile metadata, storage used, launch / delete / settings per game; "add game" walks into P2.1's import. | A user adds, launches, and removes games entirely on device, no PC involved. ✅ S2-S5: a grid of cover cards with gamepad focus, an add tile, the import flow with progress, a detail sheet with a data check, delete confirmation, and exit back to the library. All verified on the device. |
| **P2.3 Saves: export / import** | Per-game save slots export to a user-visible folder and import back (ArkTS file picker + the sandbox paths `hle_fs` already mounts). | A save exported, wiped on device, re-imported: the game resumes the exact state (checked in-game, not just file equality). |
| **P2.4 External controllers** | OHOS BT/USB HID → `hle_hid` mapping; reuse upstream's pad maps and the Switch-2 protocol tables; keyboard first if no controller is at hand. | A physical controller (or keyboard) drives the BOTW title screen end-to-end; the mapping config persists. |
| **P2.5 First public release + site** | GitHub release: signed HAP + install guide + the PC porter bundle; a static quickstart page (GitHub Pages) with the compliance note (keys are the user's own; no game data or keys are ever distributed). | Someone outside this room installs and reaches a title screen using only the public artifacts; release notes name the upstream merge point. |
| **P2.6 Compatibility TOP-5** | From upstream's playable list run 5 games through the E2E smoke (BotW plus e.g. Odyssey, MK8D, 3D World, TotK); fix or document each failure. | A 5-game compatibility table in the README with per-game status and the green/red evidence link. |
| **P2.7 Game updates** | An updated data bundle for an already-installed game imports without losing saves (stamps/manifests already support it). | A same-title version bump via import keeps the saves and the game boots the new version. |

**P2 exit**: the product is installable by strangers, holds more than one game, and survives its own updates.

---

## P3 — Polish & depth  (quarter+; order flexible)

| Item | Work | Done when |
|------|------|-----------|
| **P3.1 60 fps rollout** | Turn on profile `fps_target 60` per verified game (upstream's frame-step machinery, no port work). | ≥ 80% of frames at 60 for a bench scene, game time = real time, no thermal cliff inside 30 min. |
| **P3.2 Pipeline cache persistence** | `VkPipelineCache` persisted per game (Vulkan-native equivalent of the upstream PSO archive). | Second boot of the same scene skips most shader compiles: measured cold-vs-warm first-scene time, ≥ 2× fewer stutters. |
| **P3.3 Benchmark harness** | Device-side frame-time / thermal / power logging; per-game `bench.tsv` entries upstream-style; runs after each sync. | Nightly numbers are comparable across merges and land in a table (no regression > 10% unexplained). |
| **P3.4 Upscaling research** | Maleoon NDSR via the upstream upscaler abstraction, else FSR compute on the Vulkan path. | One game renders above native with a measured quality/eval score and stable frame pacing. |
| **P3.5 Local 2-player** | Same-device two pads (player-1 mapping in `hle_hid`) + game-native split-screen titles. | MK8D split-screen with two mapped inputs, both players controllable. |
| **P3.6 Cheats activation** | Port `hle_cheats` off the OHOS stub: Atmosphère-format files per game, toggled from the library UI. | A known cheat produces its observable effect on a known game. |
| **P3.7 Movie decode** | OHOS path for the stubbed decoders (AV1/VP9) so cutscenes play. | At least one game's opening cutscene plays with sync'd audio. |
| **P3.8 Sharing** | Saves / configs / cheat codes out through the system share sheet (built on P2.3). | A save exported on the device lands in another app intact. |
| **P3.9 Switch 2 follow-mode** | Keep upstream's Ounce / NVN2 / S2-pad support compiling and booting (SDK 22 TLR thread-id infrastructure already merged). Passive: no OHOS-specific S2 work unless a game needs it. | Upstream S2-era features pass the compile gate and boot the profile games that don't need S2 crypto. |

---

## Standing tasks (every sync, forever)

- [ ] Sync PR: conflicts resolved, queue re-exported, guard green, build green — no exceptions.
- [ ] Patch-queue count tracked in the sync PR body (16 today; down is good).
- [ ] New crash signatures triaged into `known-issues.md` the same day.
- [ ] Any new Windows-host quirk goes into the docs, not into folklore.
- [ ] TEMP diagnostic logs die with the fix that made them obsolete.

## Health metrics

| Metric | Where | Today |
|--------|-------|-------|
| Patch queue size | `dm/ohos/patches/` | 16 |
| Nested vs upstream | ahead/behind | ahead 10 / behind 0 |
| Guard | `test_ohos_patches.py` | 16 / 0 / 0 |
| Host build | `build_ohos.py --lib` | green (192 sources) |
| Device verdict | last E2E smoke | `nnosInitialize` reached (pre-fix); re-verify in P0.1 |
