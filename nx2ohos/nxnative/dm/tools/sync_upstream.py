#!/usr/bin/env python3
# Sync the OHOS port's shared files with upstream main, keeping the port syncable.
#   python tools/sync_upstream.py [--url URL] [--upstream-dir DIR] [--apply]
# Shared files (those upstream also has) are brought to upstream main; the OHOS port edits in dm/ohos/patches are re-applied on top.
# Additive OHOS files (only in our tree: dm/ohos, dm/compat, dm/gfx/vk, ...) are never touched. Upstream drift (main evolved a shared
# file we did not port-edit) is adopted as-is. Without --apply this is a dry run that only reports. Run export_ohos_patches.py first
# if you changed a port edit, and commit before syncing so the reset is recoverable.
import argparse, os, shutil, subprocess, sys, tempfile
OURS = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))          # dm/
PATCHES = os.path.join(OURS, "ohos", "patches")
ap = argparse.ArgumentParser()
ap.add_argument("--url", default="https://github.com/seedds/nxnative.git")
ap.add_argument("--upstream-dir", help="an already-extracted upstream dm/ (skips the fetch)")
ap.add_argument("--apply", action="store_true", help="actually write changes (default: dry run)")
a = ap.parse_args()

def norm(p):
    with open(p, "rb") as fh: return fh.read().replace(b"\r\n", b"\n")
def walk(root):
    out = {}
    for dp, dn, fn in os.walk(root):
        for f in fn:
            p = os.path.join(dp, f); out[os.path.relpath(p, root).replace(os.sep, "/")] = p
    return out

if a.upstream_dir:
    up = os.path.abspath(a.upstream_dir)
else:
    repo = subprocess.run(["git", "-C", OURS, "rev-parse", "--show-toplevel"], capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.strip()
    print(f"fetching {a.url} main ...")
    subprocess.run(["git", "-C", repo, "fetch", "--depth=1", a.url, "main"], check=True)
    up = tempfile.mkdtemp(prefix="ohos_up_")
    with tempfile.TemporaryFile() as tar:
        subprocess.run(["git", "-C", repo, "archive", "FETCH_HEAD", "dm/"], stdout=tar, check=True); tar.seek(0)
        subprocess.run(["tar", "-x", "-C", up], stdin=tar, check=True)
    up = os.path.join(up, "dm")

u, o = walk(up), walk(OURS)
ported, adopted, conflicts, skipped = [], [], [], 0
for rel in sorted(u):
    if rel not in o: continue                      # upstream-only: leave as our tree has it (excluded files stay excluded)
    if norm(u[rel]) == norm(o[rel]): skipped += 1; continue
    patch = os.path.join(PATCHES, rel + ".patch")
    if os.path.exists(patch):
        if a.apply:
            shutil.copyfile(u[rel], o[rel])        # pristine upstream, then re-apply the port edit
            r = subprocess.run(["patch", "-p1", "-f", "-s"], stdin=open(patch, "rb"), cwd=OURS, capture_output=True)
            if r.returncode: conflicts.append(rel); print(f"  CONFLICT {rel}: {r.stderr.decode(errors='replace').strip()[:120]}")
            else: ported.append(rel)
        else: ported.append(rel)
    else:
        if a.apply: shutil.copyfile(u[rel], o[rel])
        adopted.append(rel)
print(f"\n{'APPLIED' if a.apply else 'DRY RUN'}: {len(ported)} port edits re-applied, {len(adopted)} upstream drift adopted, {len(conflicts)} conflicts, {skipped} already in sync")
for r in ported: print("  P", r)
for r in adopted: print("  D", r)
if conflicts: sys.exit(1)
if not a.apply: print("\n(re-run with --apply to write; commit first so the reset is recoverable)")