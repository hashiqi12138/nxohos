# Game data: how it travels and where it lives

How a game's files get from the porter's pack into the app's sandbox. Build side: [BUILDING.md](BUILDING.md);
device side: [DEVICE.md](DEVICE.md); the P2.1 implementation record: [P2.1-IMPLEMENTATION.md](P2.1-IMPLEMENTATION.md).

## The model: one container, imported by the app

Game data is **one `.nxpk` file** — an 80-byte header framing a standard ustar stream:

```
offset  size  field
0       8     "NXPK" + version(1) + 3 reserved
8       16    title ID, ASCII, NUL-padded
24      48    pack stamp (sha1-hex over rel/size/mtime), NUL-padded
72      8     tar size, little-endian u64
80      ...   ustar stream: RomFS/, mods/, aoc/, shaders.dmsv, env.txt, nx2a-data.json
```

The app imports it into its own sandbox. That is the only door: the shell domain cannot write the app's
sandbox (`debug_hap_data_file`) and the app cannot read the shell's `/data/local/tmp` (`data_local_tmp`) —
both measured, see P2.1-IMPLEMENTATION.md §3. Two sources feed the door:

* **user**: the system document picker (`.nxpk` filter), then `PackImport.importPack`
* **developer**: `pack_server.py` + `hdc rport`, the app streams `/pack.nxpk` and imports it the same way

A stamp in `files/dm/.stamp` records what was imported. Equal stamps skip everything, so updating the app
(4.6 MB entry) never touches 14.75 GB of data.

## Producing a pack

The porter writes a directory tree (`mods/`, `RomFS/`, `aoc/`, `shaders.dmsv`, `env.txt`, `nx2a-data.json`).
Wrap it:

```bash
python dm/tools/pack_game.py --pack <porter pack dir> --title 01007EF00011E000 [--out <file>.nxpk]
python dm/tools/pack_game.py --pack <dir> --title <id> --check <file>.nxpk    # validate
```

`pack_game.py` generates `dm.files.tsv` in the pack dir when it is absent (via `gen_pack_manifest.py`), then
writes the container. Its stamp becomes the header's stamp, which becomes the sandbox's `.stamp`.

## Developer loop

A host-only change (the common case) never touches the data:

```bash
python dm/ohos/build_ohos.py --game <id> --lib     # ~40 s
python dm/ohos/devpush.py --game <id>              # pack 4.6 MB entry, send 1 s, install ~20 s, start
```

The app asks the server for `/stamp` (80 bytes). Equal to `.stamp` → the game starts. Measured: 12.5 s from
`aa start` to the game thread.

A data change (rewriter, profile patches, chain tools) needs one import:

```bash
python dm/tools/pack_server.py --pack <pack dir> --manifest <dm.files.tsv> --nxpk <file>.nxpk --port 28899
hdc rport tcp:28899 tcp:28899
# then start the app: it downloads (resumable), unpacks 41k files, writes the stamp
```

`hdc fport` is the wrong direction (it listens on the device, which needs that port free); `rport` listens on
the PC and the device dials out.

## The full-HAP fallback

`mkhap.py --data <pack>` still packs the data into feature modules (`entry` keeps the small fixed files,
`data1..dataN` carry `RomFS/`, `aoc/` and anything over 8 MB, largest-file-first into `--split-mb` buckets
because the signing tool refuses a HAP past 4 GB). It exists as a fallback for a one-shot integrated bundle;
it is no longer the default and every round paid 20-70 minutes for it.

Note that `bm install -p` on a folder holding only the entry HAP **removes** the installed data modules, and a
folder mixing versionCodes is rejected (`9568278 install version code not same`). That is why the slim entry
does not read module resources at all.

## Where the data lives on the device

```
/data/app/el2/100/base/dev.eden_emu.ohos/haps/entry/files/dm/     the sandbox (root passed to startGame)
    RomFS/  mods/  aoc/  shaders.dmsv  env.txt  nx2a-data.json
    .stamp                                                        the imported pack's stamp
/data/app/el2/100/base/dev.eden_emu.ohos/haps/entry/cache/
    pack.nxpk                                                     a complete container (reused, no download)
    pack.nxpk.part                                                a partial one (resumed with Range)
```

`.part` and `pack.nxpk` live under `haps/entry/cache/`, which an app update does not clear: a reinstall
resumes where it stopped rather than starting over.

## Interruption behaviour

| Event | What happens |
|---|---|
| app killed mid-download | `.part` keeps the bytes; the next start sends `Range: bytes=<have>-` |
| app updated (`bm install -r`) | `.part` survives; resumes |
| WiFi link drops (half-open socket) | a 60 s no-data watchdog destroys the request; the next start resumes |
| import interrupted | `.stamp` is not written, so the next start clears the owned dirs and imports again |
| pack already imported | stamp match: no download, no unpack, no copy |

## Pitfalls that cost real time (details in P2.1-IMPLEMENTATION.md §5)

* `requestInStream`, never `ARRAY_BUFFER` — the latter buffers the whole pack and dies around 52 MB.
* `readTimeout` is the whole request's budget: a 14.75 GB pull at ~4 MB/s needs over an hour.
* Yield during the unpack. HarmonyOS kills a foreground app whose thread blocks for 6 s
  (`THREAD_BLOCK_6S`) with no crash signal. Every 1 MB and every 5 files, `setTimeout(1)`.
* `ReadOptions.offset` is the file position — pass it on every read instead of seeking once.
* Verify `size == 80 + tarSize` before unpacking; a short file must stay `.part`.
