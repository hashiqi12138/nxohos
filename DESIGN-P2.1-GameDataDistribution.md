# P2.1 Game Data Distribution — Design Document

Status: **approved for implementation** (2026-11-15). Supersedes the rawfile-module packing for game data.
Owner: lead. Branch: `p1/pad-config` carries the working prototype (loopback import, slim entry).

## 1. Problem Statement

### 1.1 What is slow today

The current pipeline packs the entire game data (14.7 GB, 41,084 files) into five feature-module HAPs
(data1..data5). Every debug round pays for it:

| Step | Cost | Frequency |
|---|---|---|
| hvigor packs data modules | ~5 min per module | every host-data change |
| WiFi transfer of entry (598 MB, data inside) | 124 s at 5 MB/s | every round |
| `bm install -p` re-reads all six modules | ~8 min | every round |
| First-boot staging (HAP rawfile -> sandbox, 41k files) | 40-55 min | every reinstall |

Total per round: **20-70 minutes**, and none of it touches the code being changed.

### 1.2 Measured constraints (all verified on the device, 2026-11-15)

1. **SELinux two-wall separation.** The shell domain cannot write the app sandbox
   (`u:object_r:debug_hap_data_file:s0`); the app cannot read `/data/local/tmp`
   (`u:object_r:data_local_tmp:s0`). There is no direct hdc push into the sandbox.
2. **hdc's TCP protocol is the transport bottleneck, not WiFi.** Measured with 50 MB of random data:
   PC->device 7.3 MB/s, device->PC 1.8 MB/s. The asymmetric ceiling is hdc's single-stream
   application protocol (framing + checksums per chunk); the WiFi link itself pings 4-19 ms.
3. **`bm install -p` subset semantics.** Installing a folder that holds only the entry HAP *removes*
   the installed data modules; a folder mixing HAPs of different versionCodes is rejected
   (`code:9568278 install version code not same`). Version-uniform full sets only.
4. **Game data is nearly static.** RomFS (14 GB, 95% of the pack) never changes; mods/*.patched.bin
   change only with rewriter/profile work; shaders.dmsv only with chain-tool changes. A normal
   debug cycle (host C, ArkTS, UI) changes none of it.

### 1.3 What users need

- A player with no programming background must be able to install a game data pack through the GUI only.
- The pack must survive app updates without re-import.
- Multi-game support requires data to live per-title in the sandbox, removable individually.

## 2. Approved Decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | Distribution unit is a **single `.nxpk` file** (tar-based container, see §3.1) | One file to copy, share, verify; no 41k-file folder management for users |
| D2 | The app **imports the pack itself** via system `DocumentViewPicker` (FILE mode, `.nxpk` filter; FOLDER mode as a fallback for unpacked trees) | Only the app can cross the SELinux boundary into its own sandbox; the picker needs no permission declaration |
| D3 | Import unpacks the tar **streaming** into the sandbox, with progress UI | 41k HTTP requests (40 min) collapse to one sequential read |
| D4 | First import is the only bulk copy. A **stamp** (sha1 over rel/size/mtime) recorded in the sandbox skips re-import on every later launch; app updates never touch data | Game data is static (§1.2.4) |
| D5 | Developer path (devpush + loopback server) keeps the same container: the pack server grows a `/pack.nxpk` endpoint serving the same file | One format, two channels |
| D6 | The 6-module full-HAP install mode stays as a **fallback** (`mkhap --data`), not the default | Some deployments want one-shot integrated bundles; no code to delete |
| D7 | The `.nxpk` carries an 8-byte magic + version + title-ID header for validation | 10 lines, prevents "wrong file picked" confusion |
| D8 | Entry stays slim (4.6 MB measured). Game data never rides in a HAP again | The 40-min module dance disappears from every round |

## 3. Technical Design

### 3.1 The `.nxpk` container format

A tiny self-describing wrapper around a standard tar:

```
offset  size  field
0       8     magic "NXPK\x01\x00\x00\x00"   (4 bytes id, 1 byte version, 3 reserved)
8       16    title ID, ASCII ("01007EF00011E000"), NUL-padded
24      48    pack stamp, ASCII sha1-hex (the manifest stamp), NUL-padded
72      8     tar size in bytes, little-endian u64
80      ...   tar stream (ustar format, GNU tar default output)
```

Header fixed at 80 bytes for v1. Fields the app validates before importing: magic, title ID (does it
match an installed/known profile?), stamp (does the sandbox already hold this stamp?). The tar body is
produced by `tar -cf - <pack-dir>` on any platform; we do not invent a format, we frame one.

The tar entries are the porter pack contents as today: `RomFS/`, `mods/`, `env.txt`,
`nx2a-data.json`, `shaders.dmsv`, plus `dm.files.tsv` (generated if absent, §3.3).

Why tar and not zip: 41k files in zip require a central directory at the end (random access), which
means the importer cannot stream without seeking; ustar is a linear 512-byte-header-per-file format
that streams with one sequential read. Device-side extraction is ~40 lines of ArkTS (§3.2).

### 3.2 App-side: the import pipeline

New module `ets/pages/Import.ets` (or a manager class imported by Index), flow:

```
[Add Game] button
  -> DocumentViewPicker.documentSelect({ fileSuffixFilters: ['Game pack|.nxpk'], maxSelectNumber: 1 })
  -> uri of the picked file
  -> fs.openSync(uri)                         // picker URIs open directly (verified in the API docs)
  -> read 80-byte header; validate magic + title
  -> stamp check: sandbox/.stamp == header.stamp? -> done, no copy
  -> clear owned dirs (mods/, RomFS/, aoc/) as staging does today
  -> stream loop:
       read 512-byte ustar header -> name, size (octal), typeflag
       skip 2 checksum-verified zero blocks = end
       read size bytes in 1 MB chunks -> write to sandbox/<name>
       advance to 512 boundary
       every 2000 files: update Progress state (n / total from the header's count or tar size consumed)
  -> write sandbox/.stamp = header.stamp
  -> [Done] -> back to game list / start
```

No permission declaration is needed for DocumentViewPicker (it is a system UI granting a one-shot
persisted URI). The INTERNET permission stays (loopback import).

Edge cases: torn import (app killed mid-way) leaves a mismatched .stamp -> the next launch clears and
re-imports (same "removed the previous data set" semantics as today's staging). A picked file whose
magic is wrong shows an inline error, not a crash.

### 3.3 PC-side: pack production (`dm/tools/pack_game.py`)

```
python dm/tools/pack_game.py --pack <porter output dir> --title <title ID> [--out <file>.nxpk]
```

Steps: generate/verify `dm.files.tsv` in the pack dir if absent (`gen_pack_manifest` logic, already
written), `tar -cf` the tree (Python tarfile, deterministic order), prepend the 80-byte header, write
`.nxpk`. Prints stamp + size. This is the file the release page serves and what devpush's server
serves — one artifact, all channels.

### 3.4 Developer loop (devpush.py) — final shape

```
devpush --game <id>                          # host-only round (the 99% case)
  build lib -> pack 4.6 MB entry -> send (1 s) -> bm install entry alone ->
  app start -> stamp matches -> game starts in seconds

devpush --game <id> --pack <file.nxpk>       # first data landing / data change round
  serve pack.nxpk over loopback (rport) + normal entry round ->
  app start -> header stamp != sandbox stamp -> import over loopback (streamed, one connection)
```

The 41k-request loopback path (today's prototype) is removed; the loopback serves one file. Measured
hdc ceiling 7.3 MB/s applies to a single stream too, so a 14.7 GB first import over WiFi stays
~35 min **for the developer path only**; users never pay it (§3.5).

### 3.5 User paths (zero programming)

| Path | Steps | First-import cost |
|---|---|---|
| **USB** | Copy `.nxpk` from PC to phone (Downloads), in-app [Add Game] -> pick file | USB copy 30-60 MB/s off-hdc + in-app local tar read ~30+ MB/s = **minutes** |
| **LAN share** | Huawei Share / SMB / any file app lands the .nxpk on the phone, then same picker | LAN file copy at link speed, then local import |
| **Cloud drive** | Download on the phone, then same picker | Download speed + local import |

The in-app import reads from the picked URI (the phone's own storage) — never through hdc, never
through WiFi: the 1.8-7.3 MB/s hdc ceiling is a developer-loop fact, not a user fact.

### 3.6 Game library UI (the surface this plugs into)

Index grows a launcher state (self-test scene today): a list of imported games (from sandbox stamps +
nx2a-data.json), an [Add Game] button (the picker), per-game Play / Delete, and an import progress
overlay. The staging/import code paths collapse into the Import manager; the self-test scene remains
the no-data fallback. This is the P2.2 skeleton arriving through P2.1's door, not a separate sprint.

## 4. Repository Impact

| File | Change |
|---|---|
| `dm/tools/pack_game.py` | **new** — container builder (§3.3) |
| `dm/tools/pack_server.py` | extend: `/pack.nxpk` endpoint serving the container (replaces per-file serving) |
| `dm/tools/gen_pack_manifest.py` | keep — called by pack_game (already written) |
| `dm/ohos/devpush.py` | replace the 41k-file loopback import block with the single-file serve + watch |
| `dm/ohos/hap/entry/.../Index.ets` | picker entry point, import manager, progress state; remove the per-file HTTP branch |
| `dm/ohos/hap/.../module.json5` | unchanged beyond INTERNET (already in) |
| `dm/ohos/mkhap.py` | unchanged (D6 fallback); data modules stay available |
| docs (`nx2ohos/docs/ohos/GAMEDATA.md`) | rewrite the "today" section into the pack model |

Patch-queue surface: all changes are additive ArkTS/tools files — zero new upstream-shared-file
patches expected; the queue stays at 16.

## 5. Test Plan

| # | Test | Pass when |
|---|---|---|
| T1 | pack_game.py builds an .nxpk from the BOTW porter dir | header validates, tar lists 41,084 files, size ≈ 14.7 GB + 80 bytes |
| T2 | App picks the .nxpk from Downloads (device) | magic + title validated, progress bar advances, 41k files land, stamp written |
| T3 | Second launch after import | zero-copy: game starts; log shows stamp match, no import lines |
| T4 | Re-import same pack | stamp equality short-circuits (no clearing, no copy) |
| T5 | Re-import changed pack (mods changed) | old owned dirs cleared, new files land, stamp updated |
| T6 | devpush --pack loopback import | same T2 semantics over rport; one TCP stream, no 41k requests |
| T7 | Wrong file picked (a random .tar) | inline error "not a game pack", no crash, no partial writes |
| T8 | App updated via bm (entry replaced) | data + stamp untouched; game starts in seconds |
| T9 | Delete game (library action) | owned dirs removed; other games unaffected (multi-title) |

T1-T5 run on the device today; T6 exercises the developer loop; T8 is the app-update contract;
T9 lands with the library UI.

## 6. Rollout Order

1. `pack_game.py` + unit-check of the header (no device needed)
2. App import manager + picker + progress (T1-T5) — the user path is complete here
3. devpush --pack single-stream switch (T6) — developer loop matches
4. Library UI skeleton (list + add + delete + play) — P2.2 starts
5. Docs + KNOWN-ISSUES updates; mkhap full mode documented as fallback

Estimated: steps 1-2 are the core (one working session each), 3-5 follow naturally.
