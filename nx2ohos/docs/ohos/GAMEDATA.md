# Game data: where it lives and where it is going

How a game's files travel from the porter's pack into the app today, and the external-import
model that replaces it (roadmap P2.1). Build-side: [BUILDING.md](BUILDING.md);
device-side: [DEVICE.md](DEVICE.md).

## Today: game data rides inside the HAPs

A full bundle packs the porter-produced pack into the HAPs' `resources/rawfile/dm`:

```text
dm/                        (the pack's layout, identical to the Android/iOS one)
├── mods/                  # the rewritten modules (main.patched.bin, …, fixups)
├── RomFS/                 # the game's RomFS
├── aoc/                   # DLC, when the game has any
├── shaders.dmsv           # the translated shader bundle (hundreds of MB)
├── env.txt
└── settings.txt
```

`mkhap.py --data <pack>` splits it across modules:

* **entry** keeps the small fixed files — `env.txt`, `settings.txt`, the DLC index, and
  `mods/` — everything not under `RomFS/` or `aoc/` and ≤ 8 MB per file. Note
  `shaders.dmsv` rides with the **bulk** (it is per-game data, hundreds of MB), keeping
  entry small: a debug cycle replaces entry alone in seconds while the bulk stays installed
  (the Android OBB / iOS Documents split).
* **data1…dataN** (feature modules) carry the bulk — `RomFS/`, `aoc/` and any file over
  8 MB — largest-file-first into `--split-mb` (default 3500 MB) buckets, because the
  signing tool reads a HAP as a plain zip and refuses anything past 4 GB. Each `dataN`
  module contains nothing but its `rawfile/dm`: no pages, no abilities; the app reaches it
  through `createModuleContext(<module>).resourceManager`.
* **`dm.files.tsv`** is the manifest (rawfile directories cannot be listed at run time):

  ```text
  # DM_DATA <sha1 over every file's path, size and mtime>
  <relative path>\t<bytes>\t<module>
  ```

  The first line is the **stamp**. On the first start (or after the stamp changed),
  `Index.ets::stageData()` copies the whole pack file-by-file into the sandbox
  (`files/dm`, see [DEVICE.md](DEVICE.md#staging-first-boot-copies-the-pack-out-of-the-haps)),
  first clearing the three pack-owned folders (`mods/ RomFS/ aoc/`), then writing
  `files/dm/.stamp`. A start with a matching stamp copies nothing. BOTW today: ~41k files,
  about 14.7 GB over 6 signed HAPs, first staging ~40–55 min.

The host then reads the game from the sandbox: `front_ohos.c` sets `DM_ROOT=<files>/dm`,
`DM_ROMFS=<root>/RomFS`, `DM_SAVE`, `DM_TEMP`, `DM_VK_SHADERS=<root>/shaders.dmsv`, and
`env_under_root()` resolves path-valued env entries (`DM_AOC_DIR=aoc`, …) against the root
— the pack must carry relative paths only.

### Entry/bulk split (the current rule of thumb)

| | entry | bulk (data1..dataN) |
|---|---|---|
| Carries | the runtime (`libdmhost.so`), `mods/`, `env.txt`, `settings.txt`, small files | `RomFS/`, `aoc/`, `shaders.dmsv`, any file > 8 MB |
| Target size | **≤ ~60 MB** at runtime (the small runtime; keep debug rounds fast) | the rest — the game data |
| Replaced on a dev round | yes — `devpush.py` packs and installs entry alone (seconds) | no — stays installed until a new full bundle bumps the stamp |

The ~60 MB entry figure is the roadmap's locked decision #1 ("the entry HAP stays a small
runtime (~60 MB)"); a debug round's entry lands well under it. The P0.2 target allows entry
≤ 80 MB while the split is being tuned.

## Design target (P2.1): external data import

Game data **never rides inside a HAP**. This is roadmap decision #1, item P2.1 — the
architectural change of P2. It is not implemented yet; this section is the design target
the current model migrates to.

* **The app imports a porter-produced data bundle once, via the system file picker**, into
  its own storage under `games/<title-id>/`. A small per-game manifest moves next to the
  data. The entry HAP carries only the runtime.
* **Loader gains a per-game root**: the host's `DM_ROOT`-style paths resolve against
  `games/<title-id>/` instead of the single staged `files/dm`.
* **Import adopts in place** where the storage API allows it (the picker hands over
  ownership of a user folder); otherwise it copies with a progress UI — no temp
  double-copy of a 14 GB bundle.
* **Done when** (from the roadmap): two different games coexist; deleting one game's data
  leaves the other untouched; re-import after delete works; the entry HAP contains no game
  data; a 14 GB import completes with progress and no temp double-copy.

What carries over unchanged: the pack layout (`mods/ RomFS/ aoc/ shaders.dmsv env.txt`),
the stamps/manifests discipline (a game-update import keeps the saves), and the sandbox
save paths. What disappears: `--split-mb`, the `dataN` feature modules, the 4 GB
signed-HAP ceiling as a data constraint, and the first-boot staging copy.

Interim consequences to remember while both models exist:

* A data set change still means a full `mkhap.py --data` rebuild + full install (one 4 GB
  split set per module, bm's `-w 600`, the staging re-copy on the device).
* `bm install -p` installs the bundle as a whole; a dataN-only change still re-installs
  the group. Only entry changes are cheap (`devpush.py`).
* The app's storage adoption in P2.1 removes the WiFi 15 GB transfer entirely for data
  updates — the import is device-side.
