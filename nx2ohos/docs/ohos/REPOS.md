# Repo topology: upstream, the nested fork, the outer mirror

Who pulls from whom, where a change lands, and how the daily sync keeps it all green.
Build/device docs: [BUILDING.md](BUILDING.md), [DEVICE.md](DEVICE.md).

## The two repos and the mirror

```text
upstream  github.com/seedds/nxnative            (fetch + merge ONLY — never push)
   │  fetch + merge upstream/main
   ▼
nested    nx2ohos/nxnative/                     a true fork: upstream's full history + the OHOS port
   │  git add nx2ohos/nxnative path-by-path     (never a gitlink/submodule)
   ▼
outer     this monorepo, github.com/hashiqi12138/nxohos   (the only push target)
```

* **Upstream `seedds/nxnative`** is the source of everything generic (frame pacing, HLE,
  profiles, the rewriter chain). We fetch and merge it; pushing to it is forbidden — the
  remotes are configured the same way in both repos: `origin` = `hashiqi12138/nxohos`,
  `upstream` = `seedds/nxnative`.
* **The nested fork** `nx2ohos/nxnative/` is a *separate git repository* living inside the
  outer monorepo's tree (it has its own `.git`). All OHOS development happens here, on
  `main`. Its non-additive changes to upstream-shared files are carried as a patch queue
  (`dm/ohos/patches/`, currently **16 patches**), guarded by
  `dm/tools/test_ohos_patches.py`. Never `cd` into it expecting outer-repo commands to
  work, and never register it as a submodule.
* **The outer monorepo** (`hashiqi12138/nxohos`) mirrors the nested tree **file-by-file**:
  the nested files appear as ordinary tracked paths (`nx2ohos/nxnative/...`), never a
  gitlink. Only the outer repo is pushed.

### The patch queue (16 patches)

Files under `dm/ohos/patches/` mirror the paths they patch (`--- a/host/crash.c` etc.):

```text
dm/ohos/patches/gfx/nvn/hle_nvn.c.patch        dm/ohos/patches/hle_table.c.patch (host/)
dm/ohos/patches/gfx/nvn/nvn_capture.c.patch    dm/ohos/patches/loader.c.patch
dm/ohos/patches/host/crash.c.patch             dm/ohos/patches/probe.c.patch
dm/ohos/patches/host/dm.h.patch                dm/ohos/patches/thread.c.patch
dm/ohos/patches/host/hle.h.patch               dm/ohos/patches/hle_bigstack.c.patch
dm/ohos/patches/host/hle_fdstat.c.patch        dm/ohos/patches/hle_lifecycle.c.patch
dm/ohos/patches/host/hle_offline.c.patch       dm/ohos/patches/hle_thread.c.patch
dm/ohos/patches/tools/patch_nso.py.patch       dm/ohos/patches/tools/nso_asm.py.patch
```

Rules of the queue:

* **The port surface only shrinks.** New OHOS work lands as additive files under
  `dm/ohos/*` whenever possible; the queue must trend down over time.
* A shared file is only patched when its diff adds OHOS markers (the export tool greps
  added lines for `__APPLE__|DM_OHOS|DM_SECTION|DM_ASM|DM_UC_|dm_fd_path|dm_uc\.h|OHOS|HarmonyOS|TARGET_OS_IPHONE`).
  A markerless diff is upstream drift → no patch; sync takes main's version.
* Binary files and the additive dirs (`dm/ohos`, `dm/compat`, `dm/gfx/vk`) are never patched.
* The guard `python dm/tools/test_ohos_patches.py` proves each patch applies to upstream
  main **and** reproduces the working file exactly; `build_ohos.py --check-patches` runs it
  before compiling. Today: `guard: 16 patches checked, 0 failures, 0 drift`.

## How a change flows

1. **Commit in the nested repo** (`nx2ohos/nxnative/`), on a branch or `main`.
2. **Merge to nested `main`** (normal fork work; upstream merges come in via
   `sync_upstream.py`, below).
3. **Mirror to the outer repo**: from the monorepo root, `git add nx2ohos/nxnative/<path>`
   for exactly the files the change touched — **path-by-path adds, never a gitlink**
   (`git add nx2ohos/nxnative` as a whole is fine as long as git treats it as normal paths;
   if it ever resolves to a single gitlink entry, stop — something is wrong with the
   nesting). The outer repo has no submodule entry for the nested repo.
4. **Outer commit and push** to `origin` = `hashiqi12138/nxohos`.

Commit identity: `git -c user.name=hashiqi12138 -c user.email=liujieahu@163.com commit` in
the outer repo (the nested repo's own convention is
`git -c user.name=seedds -c user.email=seedds@gmail.com commit`, and it commits only when
asked). Never push upstream.

## The sync procedure (daily; P1.1 automates it)

All commands in the **nested repo**:

```powershell
# 1. fetch and merge upstream main:
python dm/tools/sync_upstream.py [--remote upstream] [--ref main] [--repo <dir>] [--dry-run]

# 2. on merge conflicts in the patch-queue files, resolve by hand, git add + commit.

# 3. re-export the queue to match the merged tree:
python dm/tools/export_ohos_patches.py

# 4. guard + build:
python dm/tools/test_ohos_patches.py
python dm/ohos/build_ohos.py --game <id> --lib --check-patches
```

Verified behavior of the tools:

* `sync_upstream.py` requires a clean working tree (it stops and says so), runs
  `git fetch <remote>` then `git merge <remote>/<ref>` as one merge commit
  (`--no-edit`), and refuses a shallow fetch (the nested repo keeps upstream's full
  history). `--dry-run` lists and diffs what upstream holds beyond our main without
  merging. On a conflicted merge it prints `git diff --name-only --diff-filter=U` and tells
  you the exact next steps (`export_ohos_patches.py` + `test_ohos_patches.py`, or
  `git merge --abort`).
* **Docs-class conflicts** (upstream deleted a doc/moved a path that our tree also touched)
  resolve by **keeping the deletion**: `git rm <file>`, continue the merge. This is the
  standing auto-resolution the sync bot is built around; a human is called only on real
  content conflicts.
* `export_ohos_patches.py` re-exports the queue from the merged tree against the nested
  repo's `upstream/main` ref (no network; an extracted `dm/` can be passed as an argument
  instead). It prints three sections: PORT EDITS (patched), UPSTREAM DRIFT (take main, no
  patch), BINARY (skip).
* The guard must end `0 failures` and the queue count must not grow past 16 without a
  written justification (the roadmap's standing rule).

## Health metrics (from ROADMAP.md, keep them true)

| Metric | Where | Today |
|--------|-------|-------|
| Patch queue size | `dm/ohos/patches/` | 16 |
| Nested vs upstream | ahead/behind | ahead 10 / behind 0 |
| Guard | `test_ohos_patches.py` | 16 / 0 / 0 |
| Host build | `build_ohos.py --lib` | green (192 sources) |
