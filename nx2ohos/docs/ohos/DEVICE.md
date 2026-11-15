# The device loop: hdc, install, staging, logs

Everything between "a signed HAP exists on the PC" and "the log tells me what happened".
Build-side docs: [BUILDING.md](BUILDING.md). Repo topology: [REPOS.md](REPOS.md).

The test device (until P3): one Maleoon 910 phone, hdc `192.168.2.158:42213` over WiFi.
Current model observed on the log: HBN-AL80, 2844×1260.

## hdc

The OHOS adb counterpart. It ships inside DevEco Studio; the scripts use exactly this path:

```text
<DevEco Studio>\sdk\default\openharmony\toolchains\hdc.exe
```

e.g. `E:\Program Files\Huawei\DevEco Studio\sdk\default\openharmony\toolchains\hdc.exe`
(porter also honors `$HDC` — a program or its folder — as an override).

Connect:

```powershell
& $hdc tconn 192.168.2.158:42213
& $hdc list targets        # shows TCP/Connected
```

* The WiFi connection **drops when idle** — re-run `tconn` before each command batch
  (observed on this device; the build guide says the same).
* With several targets, every command needs `-t 192.168.2.158:42213` or hdc answers
  `need connect-key`.
* In Git Bash, `hdc file send/recv` paths get mangled — use PowerShell with absolute paths.

## The scheduled wake task (PC side)

The PC runs a **scheduled task that wakes the phone** so that an `aa start` round is possible
at any hour. Rule of the loop:

* A locked/sleeping phone refuses app starts. Wake the screen with
  `power-shell wakeup` (the device shell command); then **unlock by hand** if the device has
  a PIN — the wake task only lights the screen, it does not bypass the lock.
* `aa start` fails with error **10106102** while the screen is locked. That code is not a
  build problem: wake the device and retry.

## Install rules

Single HAP (small packs, canonical build):

```powershell
& $hdc -t $target install -r <entry-default-signed.hap>     # -r: replace a previous install
```

Split pack (entry + data1..dataN — anything over the 4 GB signed-HAP limit). The whole
bundle installs **as one** from a folder:

```powershell
$remote = "/data/local/tmp/nx2a-install"
& $hdc -t $target shell "rm -rf $remote; mkdir -p $remote"
Get-ChildItem $haps -Filter *.hap | ForEach-Object { & $hdc -t $target file send $_.FullName "$remote/$($_.Name)" }
& $hdc -t $target shell bm install -p $remote -u 100 -r -w 600
# some builds refuse -u 100 — retry without it:
& $hdc -t $target shell bm install -p $remote -r -w 600
```

Verified details (from `porter/platform_ohos.py` and the on-device records):

* `-w 600` is required: bm's default wait is 180 s and a 15 GB bundle takes ~25 min to
  unpack (`code:9568392 operation time out` without it); 600 s is bm's maximum.
* `-u 100` names the user; drop it and let bm pick when the device refuses.
* `-r` replaces the previous install of the same bundle (keeps the sandbox — a versionCode
  bump re-installs in place and staging's stamp decides what to copy).
* Do **not** install a split pack module-by-module and do not `hdc install` the `.app`
  (it carries the unsigned copies; hdc refuses it with `no signature file`).
* **versionCode must increase** or the install is refused as a downgrade
  (`install version downgrade`, code 9568263). `mkhap.py`/`devpush.py` set it to Unix
  seconds by default; if the device already holds a higher one (e.g. from another build
  dir, or a manual `DM_VERSION_CODE=200000000N`), pass a higher
  `DM_VERSION_CODE` / `--version-code`.
* After `bm install`, stop and start the app:

```powershell
& $hdc -t $target shell aa force-stop dev.eden_emu.ohos
& $hdc -t $target shell aa start -b dev.eden_emu.ohos -a EntryAbility
```

Start `entry` only — the `dataN` modules are rawfile containers with no ability.

* Right after an install the device's security scan can answer `start ability successfully`
  while nothing starts (measured on the cloud device: `pidof` empty); wait a few seconds and
  retry `aa start` until `pidof dev.eden_emu.ohos` shows a PID.

**Dev rounds**: `devpush.py` installs **entry alone** over the installed bundle (the bulk
`dataN` modules stay installed — the Android OBB / iOS Documents split), which turns a
round into minutes. See [BUILDING.md](BUILDING.md#the-three-commands) for its flags.

## Staging: first boot copies the pack out of the HAPs

A data-carrying build packs the game under the modules' `resources/rawfile/dm`; rawfile
directories cannot be listed at run time, so `dm.files.tsv` (written by `mkhap.py`) is the
manifest. On the first start `Index.ets::stageData()` copies every file into the sandbox:

```text
/data/app/el2/100/base/dev.eden_emu.ohos/haps/entry/files/dm/     (device view)
/data/storage/el2/base/haps/entry/files/dm/                      (in-app view)
```

* **First boot after a full install: ~41k files, 40–55 minutes.** The app streams each file
  through a 1 MiB buffer (`getRawFd` + `lseek`, never whole-file `getRawFileContent` — the
  RomFS holds files of hundreds of MB) and logs `staged N files` every 200 files.
* The manifest's first line `# DM_DATA <sha1>` is a **stamp**; the sandbox keeps it in
  `.stamp`. A start with a matching stamp copies **nothing** (dev rounds rely on this:
  devpush's entry carries the same stamp). A changed data set (new stamp) clears the three
  pack-owned folders `mods/ RomFS/ aoc/` first, then re-stages.
* `dataN` files are read through `createModuleContext(<module>).resourceManager` (the
  manifest's third column names the module per file).
* A failed or interrupted staging returns `fail`, logs `staging failed: …` and the game is
  **not** started on a half copy; no stamp is written. If you kill the app mid-staging,
  just start again — the copy restarts from zero for the current stamp.
* No manifest at all (`staging: this build carries no data pack`) means the build is the
  data-less canonical one; the app runs the self-test scene, which is the correct behavior.
* The game starts only when `mods/main.patched.bin` and `RomFS` exist in the sandbox.

## Reading hilog

The host logs over hilog with domain `0x0201`, tag **dmhost** (`dm/ohos/ohos_log.c`;
stderr is a black hole in an app — see [KNOWN-ISSUES.md](KNOWN-ISSUES.md)):

```powershell
& $hdc -t $target shell hilog -x -T dmhost      # the host's lines (native module + page)
& $hdc -t $target shell hilog -r                # clear the buffer before a run
& $hdc -t $target shell hilog -x -P <PID>       # everything from this process (the page's
                                                #  console.info needs -P; pidof dev.eden_emu.ohos)
```

* Clear (`hilog -r`) right before `aa start` so the verdict is only the new run's lines.
* Judge only the **current PID** (`pidof dev.eden_emu.ohos`) — an old process's lines linger.
* Useful greps: `starting the game`, `staged`, `frames presented`, `[demo]`, `CRASH sig=`.
* `devpush.py` does all of this: start, wait, `hilog -x -T dmhost`, print the tail.

## WiFi transfer rates to expect

`hdc file send` over WiFi to this device runs at **2–8 MB/s**:

| Payload | Time |
|---|---|
| entry HAP (~60 MB, a dev round) | **~15–150 s** |
| full split pack (15 GB, once per game) | tens of minutes to hours — let the porter `--install` stage run, or batch `file send` per HAP |

(2 MB/s worst → 60 MB / 2 MB/s = 30 s; best 8 MB/s → ~8 s; observed variance including
re-tconn after idle drops gives the 15–150 s envelope.)

## Quick reference (one debug round)

```powershell
$env:PYTHONUTF8 = "1"
& $hdc tconn 192.168.2.158:42213
python nxnative/dm/ohos/devpush.py --game 01007EF00011E000
# (build_ohos + entry pack + send + bm install -p …/nx2a-entry -r -w 600 + aa start + hilog tail)
```

If `aa start` answers **10106102**: the screen is locked — `power-shell wakeup`, unlock
by hand if PIN-locked, retry. If install answers `install version downgrade`: bump the
versionCode. If the app "started" but `pidof` is empty: the post-install scan window —
retry after a few seconds.
