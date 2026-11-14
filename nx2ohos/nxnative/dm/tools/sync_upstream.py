#!/usr/bin/env python3
# Sync the nested nxnative fork with upstream main the git way: fetch, then merge.
#   python tools/sync_upstream.py [--remote upstream] [--ref main] [--repo DIR] [--dry-run]
# The nested repo (nx2ohos/nxnative) holds upstream's full history plus the OHOS port on top, so a sync
# is an ordinary fork sync: `git fetch upstream` + `git merge upstream/main`, one merge commit that keeps
# upstream's new commits in our history. Additive OHOS files (dm/ohos, dm/compat, dm/gfx/vk) cannot
# conflict. Port edits (the files of dm/ohos/patches) conflict only where upstream moved the same lines:
# the merge then stops with the files listed; resolve them by hand, git add + commit, then re-run
# export_ohos_patches.py and test_ohos_patches.py so the patch queue and the guard match the merged tree.
# --dry-run fetches and reports what upstream holds beyond our main, without merging.
import argparse, os, subprocess, sys
ap = argparse.ArgumentParser()
ap.add_argument("--repo", default=os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))   # nxnative/
ap.add_argument("--remote", default="upstream")
ap.add_argument("--ref", default="main")
ap.add_argument("--dry-run", action="store_true")
a = ap.parse_args()
IDENT = ["-c", "user.name=seedds", "-c", "user.email=seedds@gmail.com"]

def git(args, check=True, ident=False):
    r = subprocess.run(["git"] + (IDENT if ident else []) + ["-C", a.repo] + args,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and r.returncode: sys.exit(f"sync_upstream.py: git {' '.join(args)} failed:\n{r.stderr.strip()}")
    return r

upref = f"{a.remote}/{a.ref}"
print(f"fetching {a.remote} ({a.ref}) ...")
git(["fetch", a.remote])
if git(["rev-parse", "--verify", "--quiet", upref], check=False).returncode:
    sys.exit(f"sync_upstream.py: no {upref} after fetch; check the remote and branch names")
new = git(["rev-list", "--count", f"HEAD..{upref}"]).stdout.strip()
print(f"{new} new upstream commit(s) on {upref} ({git(['rev-parse', '--short', upref]).stdout.strip()})")
if new == "0":
    print("already in sync")
    sys.exit(0)
if a.dry_run:
    print(git(["log", "--oneline", f"HEAD..{upref}"]).stdout, end="")
    print(git(["diff", "--stat", f"HEAD...{upref}"]).stdout, end="")
    print("(dry run: re-run without --dry-run to merge)")
    sys.exit(0)
dirty = git(["status", "--porcelain"]).stdout.strip()
if dirty:
    print(dirty)
    sys.exit("sync_upstream.py: working tree not clean; commit or stash first, then sync")
r = git(["merge", "--no-edit", upref], check=False, ident=True)
print((r.stdout or "") + (r.stderr or ""), end="")
if r.returncode:
    print(git(["diff", "--name-only", "--diff-filter=U"]).stdout, end="")
    sys.exit("sync_upstream.py: merge stopped on conflicts; resolve the files above, git add them, commit,"
             "\nthen re-run export_ohos_patches.py + test_ohos_patches.py (or git merge --abort to give up)")
print("merged; if any patch target moved, re-run export_ohos_patches.py (default upstream = upstream/main)"
      "\nand test_ohos_patches.py to refresh and verify the patch queue")
