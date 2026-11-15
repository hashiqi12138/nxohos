# Building the host and the HAP on Windows

How to go from a clean Windows PC to a signed HAP on the device. Paths below assume the
monorepo checkout (this repo) with the nested fork at `nx2ohos/nxnative/`; every script lives
under `nx2ohos/nxnative/dm/`. Examples use BOTW's title ID, `01007EF00011E000`.

For the device side (install, wake, hilog, transfer rates) see [DEVICE.md](DEVICE.md). For the
repo topology and the sync flow see [REPOS.md](REPOS.md).

## Prerequisites

| What | Why | Notes |
|---|---|---|
| Python 3.13 | porter, the NSO rewriter, the build/pack scripts | The repo asks 3.11+; the OHOS tooling is developed on 3.13. |
| `capstone` 5.x (`pip install "capstone>=5,<6"`) | The NSO rewriter (`dm/tools/patch_nso.py`) | It hard-refuses Capstone 6 at import: `[RED] Capstone … is unsupported; the rewriter requires Capstone 5.x`. |
| DevEco Studio + its OHOS SDK | hvigor, Node, JBR 21, the signing material, `hdc` | The scripts auto-find `E:\Program Files\Huawei\DevEco Studio` (also `$DEVECO_HOME`, `D:\dev-echo\DevEco Studio`, `C:\Program Files\Huawei\DevEco Studio`). The SDK dir must hold `native/llvm/bin/clang(.exe)` and `native/sysroot/`. Override with `OHOS_SDK` / `DEVECO_SDK_HOME` / `HOS_SDK_HOME` or `--sdk` if your install differs. |
| DevEco automatic signing for `dev.eden_emu.ohos` | HAP signing | Open `dm/ohos/hap` in DevEco once and enable automatic signing; the keystore password stays in the IDE's secure storage. Signing needs DevEco's own JBR 21 (`<DevEco>\jbr`) — a system Java 11 cannot sign. `mkhap.py` sets `JAVA_HOME` to it automatically. |
| `glslc` (a Vulkan SDK's, or `$GLSLC`) | The backend's own shaders in `dm/gfx/vk/shaders/` | `build_ohos.py` exits with `needs glslc` when a shader header is missing. Searched under `E:/build-tool/VulkanSDK`, `C:/VulkanSDK`, … |
| `PYTHONUTF8=1` | The console is cp936; the rewriter's output contains non-ASCII (U+2212 minus signs in cut reasons) | Set it in every shell; this is a documented host quirk, not something the scripts set for you. |
| `patch` (GNU patch) on PATH | The patch-queue guard applies `.patch` files with `patch -p1` | Ships with Git for Windows. |

Recommended shell setup (PowerShell):

```powershell
$env:PYTHONUTF8 = "1"
# optional, the scripts find the default install anyway:
$env:DEVECO_HOME = "E:\Program Files\Huawei\DevEco Studio"
$env:OHOS_SDK   = "$env:DEVECO_HOME\sdk\default\openharmony"
```

## The three commands

Run from the **monorepo root** (`nx2ohos/`), so scripts are addressed as
`python nxnative/dm/...`. From inside the nested repo, drop the `nxnative/` prefix.

### 1. Build the host (`libdmhost.so`)

```powershell
python nxnative/dm/ohos/build_ohos.py --game 01007EF00011E000 --lib --check-patches
```

Compiles the host (`dm/host`), graphics (`dm/gfx`, `dm/gfx/nvn`, `dm/gfx/tex`), the shared
Vulkan replay (`dm/gfx/vk`), the POSIX/Darwin compat layer (`dm/compat`) and the OHOS
front end (`dm/ohos/*.c`) with the OHOS SDK's clang for `aarch64-linux-ohos`.

Verified flags:

| Flag | Meaning |
|---|---|
| `--game <id>` | required; the title ID. Also generates the profile header via `dm/games/game.py header`. |
| `--lib` / `--exe` | build `libdmhost.so` (the HAP's native module) and/or `dmhost` (a headless shell executable). Neither given → both. |
| `--check-patches` | run `dm/tools/test_ohos_patches.py` first; the build fails if the patch queue no longer reproduces from upstream main + `dm/ohos/patches/` (stale queue or accidental edit). |
| `--only-compile` | compile every source, report errors, do not link. |
| `--keep-going` | do not stop at the first failing source. |
| `--release` | `-O`-level build without `-g`, output under `out/<game>-release/`, `.so` stripped. Debug is the default. |
| `--jobs N`, `-O <n>`, `--gfx vk\|null`, `--out <dir>`, `--sdk <dir>`, `--upstream-dir <dir>` | parallelism, optimization level, backend, output/toolchain overrides (`--upstream-dir` feeds `--check-patches`). |

Outputs land in `dm/ohos/out/<title ID>/` (release: `dm/ohos/out/<title ID>-release/`):

```text
dm/ohos/out/01007EF00011E000/
├── dmhost            # the shell executable (headless)
├── libdmhost.so      # the HAP's native module — what mkhap.py packs
├── obj/              # objects + .d files (incremental; wiped when flags change)
└── gen/              # generated headers (game_profile.h, shader .spv.h)
```

### 2. The patch queue: export and guard

The OHOS port's edits to upstream-shared files are kept as a patch queue in
`dm/ohos/patches/` (16 patches today; see [REPOS.md](REPOS.md)):

```powershell
# re-export the queue from the working tree vs upstream/main:
python nxnative/dm/tools/export_ohos_patches.py          # or: export_ohos_patches.py <upstream dm/ dir>

# guard: every patch applies to upstream main AND reproduces the working file:
python nxnative/dm/tools/test_ohos_patches.py [--upstream-dir <dir>]
```

* `export_ohos_patches.py` diffs every shared file against upstream main (extracted from the
  nested repo's `upstream/main` ref — no network). A diff whose added lines carry OHOS
  markers (`__APPLE__`, `DM_OHOS`, `OHOS`, …) becomes `dm/ohos/patches/<rel>.patch`;
  a markerless diff is upstream drift and is **not** patched (sync takes main's version).
  Binary files are never text-patched. Additive OHOS dirs (`dm/ohos`, `dm/compat`,
  `dm/gfx/vk`) are not patched either.
* `test_ohos_patches.py` (the guard) checks three things: every patch applies cleanly to
  upstream main, applying it reproduces the working file exactly (LF-normalized), and the
  additive dirs exist. Output: `guard: 16 patches checked, 0 failures, 0 upstream-drift files
  pending sync`; exit code 1 on any failure. Never shallow-fetch upstream here — the guard
  extracts `upstream/main` from the nested repo's own ref.

`build_ohos.py --check-patches` runs the guard for you before compiling; CI (P1.2) and the
daily sync (P1.1) run it standalone.

### 3. Pack the HAP

**Full bundle** (host + game data; what you install once per game):

```powershell
python nxnative/dm/ohos/mkhap.py --game 01007EF00011E000 --data <pack dir>
```

**Debug round** (host changes only; the data modules stay installed on the device):

```powershell
python nxnative/dm/ohos/devpush.py --game 01007EF00011E000
```

`mkhap.py` verified flags and env:

| Flag / env | Meaning |
|---|---|
| `--game <id>` | required; names the output folder and the ASCII build copy. |
| `--data <dir>` | the porter-produced pack (`mods/ RomFS/ aoc/ shaders.dmsv env.txt …`) packed into the modules' `resources/rawfile/dm`. Omit for a data-less (canonical) HAP. |
| `--lib <file>` | pack this `libdmhost.so` instead of `dm/ohos/out/<game>/libdmhost.so`. |
| `--split-mb <n>` | the most data one HAP may carry; default 3500. |
| `--install` | after packing, also install (`hdc install -r`) and start on the attached device, then wait for hilog. Single-HAP case; for a split pack use `python -m porter port … --install` or the manual `bm install -p` flow (see [DEVICE.md](DEVICE.md)). |
| `--keep-build` | keep the ASCII build dir (for debugging the hvigor project). |
| `--out <dir>` | output dir; default `dm/ohos/out`. |
| `DM_VERSION_CODE` | override the versionCode (default: Unix seconds — always above any installed build). |
| `DM_HAP_BUILD_DIR` | the ASCII build dir; default `F:\ohos_build_dmhost_<game>`. The name must start with `ohos_build`; it is emptied on every run. |

`mkhap.py` hard rules (measured, in the script):

* hvigor refuses a non-ASCII project path → the project is copied to an ASCII dir first
  (drive root, per above).
* `libdmhost.so` must be in `entry/libs/arm64-v8a` before hvigor runs or it is not packed.
* The signing tool reads a HAP as a **plain zip and is not zip64-aware**: a signed HAP must
  stay under **4 GB** (`find zip central directory failed` past it). Bulk data over
  `--split-mb` is therefore split over feature modules `data1, data2, …`, each built and
  signed by its own `assembleHap` run. A rawfile cannot be split, so a single file over the
  limit aborts the pack.
* Project mode has no `assembleHap` and `assembleApp` packs a giant `.app` out of the
  *unsigned* copies — never use it; module mode only, one module per run.
* The build disk must hold the pack about **twice**: mkhap checks
  `free ≥ 2 × pack + 512 MB` on the build dir's disk and exits with a message telling you to
  point `DM_HAP_BUILD_DIR` elsewhere. A 15 GB pack needs ~31 GB free. (Files are hardlinked
  in when the build disk is the pack's own disk, but the check is worst-case.)

`devpush.py` verified flags: `--game <id>` (required), `--lib <file>`, `--mods <dir>`,
`--no-build`, `--no-start`, `--keep`, `--version-code <n>`. It builds the lib (unless
`--no-build`), re-packs **entry only** in the existing `F:\ohos_build_dmhost_<game>` tree
(a full `mkhap.py --data` install must have run once), pushes and installs entry alone,
starts the app and tails the log. Its entry carries the same `dm.files.tsv` stamp as the
full install, so device-side staging copies nothing.

## Where outputs land

| Command | Output |
|---|---|
| `build_ohos.py` | `dm/ohos/out/<game>/{dmhost, libdmhost.so}` (release: `…-release/`) |
| `mkhap.py`, single module | `<out>/dmhost-signed.hap` (or `dmhost-unsigned.hap`) |
| `mkhap.py`, split pack | `<out>/dmhost-haps/` — `entry-default-signed.hap` + `data1..dataN-default-signed.hap`, one signed HAP per module |
| `devpush.py` | `F:\ohos_build_dmhost_<game>\entry\build\default\outputs\default\entry-default-signed.hap`, then pushed to the device (see [DEVICE.md](DEVICE.md)) |

## Checklist for a from-scratch machine

1. Install Python 3.13 + `capstone>=5,<6`; set `PYTHONUTF8=1`.
2. Install DevEco Studio (default path `E:\Program Files\Huawei\DevEco Studio`); let it
   download its SDK; open `dm/ohos/hap` once and enable automatic signing for
   `dev.eden_emu.ohos`.
3. `python dm/tools/test_ohos_patches.py` — guard green (`16 checked, 0 failures`).
4. `python dm/ohos/build_ohos.py --game <id> --lib --check-patches` — host green.
5. Porter the game (`python -m porter port <game folder> -k <prod.keys> --platform hap`)
   or pack an existing pack with `mkhap.py --data`.
6. Install on the device per [DEVICE.md](DEVICE.md); afterwards use `devpush.py` per round.
