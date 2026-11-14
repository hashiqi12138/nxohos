#!/usr/bin/env python3
# Guard for the OHOS port's syncability with upstream main.
#   python tools/test_ohos_patches.py [--upstream-dir DIR]
# Checks: (1) every patch in dm/ohos/patches applies cleanly to upstream main; (2) applying it reproduces the working port-edited
# file exactly (LF-normalized); (3) the additive OHOS dirs are present. Upstream drift (a shared file that differs from main but has
# no patch) is reported as pending-sync, not a failure — run sync_upstream.py to adopt main. Exits non-zero if a patch is stale or broken.
import argparse, os, shutil, subprocess, sys, tempfile
OURS = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))          # dm/
PATCHES = os.path.join(OURS, "ohos", "patches")
ap = argparse.ArgumentParser()
ap.add_argument("--upstream-dir", help="an already-extracted upstream dm/ (skips the fetch)")
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
    subprocess.run(["git", "-C", repo, "fetch", "--depth=1", "https://github.com/seedds/nxnative.git", "main"], check=True)
    up = tempfile.mkdtemp(prefix="ohos_guard_")
    with tempfile.TemporaryFile() as tar:
        subprocess.run(["git", "-C", repo, "archive", "FETCH_HEAD", "dm/"], stdout=tar, check=True); tar.seek(0)
        subprocess.run(["tar", "-x", "-C", up], stdin=tar, check=True)
    up = os.path.join(up, "dm")
u, o = walk(up), walk(OURS)
fails = []
patches = sorted(os.path.relpath(os.path.join(dp, f), PATCHES).replace(os.sep, "/")
                 for dp, dn, fn in os.walk(PATCHES) for f in fn if f.endswith(".patch"))
tmp = tempfile.mkdtemp(prefix="ohos_guard_apply_")
for p in patches:
    rel = p[:-len(".patch")]
    if rel not in u: fails.append(f"{p}: target not in upstream"); continue
    dst = os.path.join(tmp, rel); os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copyfile(u[rel], dst)
    r = subprocess.run(["patch", "-p1", "-f", "-s"], stdin=open(os.path.join(PATCHES, p), "rb"), cwd=tmp, capture_output=True)
    if r.returncode: fails.append(f"{p}: does not apply to upstream ({r.stderr.decode(errors='replace').strip()[:80]})"); continue
    if rel not in o: fails.append(f"{p}: patched file missing from working tree"); continue
    if norm(dst) != norm(o[rel]): fails.append(f"{p}: stale — applying to upstream does not reproduce the working file (re-run export_ohos_patches.py)")
for d in ("ohos", "compat", "gfx/vk"):
    if not os.path.isdir(os.path.join(OURS, d)): fails.append(f"additive dir missing: {d}")
drift = [rel for rel in sorted(o) if rel in u and not rel.startswith("ohos/patches/")
         and norm(o[rel]) != norm(u[rel]) and not os.path.exists(os.path.join(PATCHES, rel + ".patch"))
         and b"\0" not in norm(o[rel])]
print(f"guard: {len(patches)} patches checked, {len(fails)} failures, {len(drift)} upstream-drift files pending sync")
for f in fails: print("  FAIL", f)
for d in drift: print("  drift", d)
shutil.rmtree(tmp, ignore_errors=True)
sys.exit(1 if fails else 0)