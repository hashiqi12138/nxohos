#!/usr/bin/env python3
# Export the OHOS port's modifications to upstream shared files as a patch queue (dm/ohos/patches/<rel>.patch).
#   python tools/export_ohos_patches.py [upstream dm/ dir]   (default: the nested repo's upstream/main ref)
# A shared file is a PORT EDIT when its diff adds OHOS-specific markers (cross-platform macros / compat shims / __APPLE__ guards);
# those become unified-diff patches (upstream-main -> working, LF-normalized, a/<rel> b/<rel> headers; apply from dm/ with patch -p1).
# A modified shared file WITHOUT such markers is upstream DRIFT (main evolved it after our baseline): it is NOT patched - the sync
# script takes main's version. Binary files are never text-patched. Additive OHOS files (dm/ohos, dm/compat, dm/gfx/vk, ...) are not patched.
import os, re, subprocess, sys, tempfile, difflib
OURS = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))          # dm/
if len(sys.argv) > 1:
    UP = os.path.abspath(sys.argv[1])
else:
    # The nested repo is a real fork: extract upstream main's dm/ from its upstream/main ref (no network).
    repo = subprocess.run(["git", "-C", OURS, "rev-parse", "--show-toplevel"], capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.strip()
    if subprocess.run(["git", "-C", repo, "rev-parse", "--verify", "--quiet", "upstream/main"], capture_output=True).returncode:
        sys.exit("export_ohos_patches.py: no upstream/main ref in the nested repo - run dm/tools/sync_upstream.py first, or pass an upstream dm/ dir")
    _up = tempfile.mkdtemp(prefix="ohos_export_")
    with tempfile.TemporaryFile() as _tar:
        subprocess.run(["git", "-C", repo, "archive", "upstream/main", "dm/"], stdout=_tar, check=True); _tar.seek(0)
        subprocess.run(["tar", "-x", "-C", _up], stdin=_tar, check=True)
    UP = os.path.join(_up, "dm")
PATCHES = os.path.join(OURS, "ohos", "patches")
MARKER = re.compile(r"__APPLE__|DM_OHOS|DM_SECTION|DM_ASM|DM_UC_|dm_fd_path|dm_uc\.h|OHOS|HarmonyOS|TARGET_OS_IPHONE")
def norm_bytes(p):
    with open(p, "rb") as fh: return fh.read().replace(b"\r\n", b"\n")
def norm_lines(p): return norm_bytes(p).decode("utf-8", "replace").splitlines(keepends=True)
def walk(root):
    out = {}
    for dp, dn, fn in os.walk(root):
        for f in fn:
            p = os.path.join(dp, f); out[os.path.relpath(p, root).replace(os.sep, "/")] = p
    return out
o, u = walk(OURS), walk(UP)
ported, drift, binary = [], [], []
for rel in sorted(o):
    if rel not in u or rel.startswith("ohos/patches/"): continue
    ob, ub = norm_bytes(o[rel]), norm_bytes(u[rel])
    if ob == ub: continue
    if b"\0" in ob or b"\0" in ub: binary.append(rel); continue
    a, b = ub.decode("utf-8", "replace").splitlines(keepends=True), ob.decode("utf-8", "replace").splitlines(keepends=True)
    diff = list(difflib.unified_diff(a, b, fromfile="a/" + rel, tofile="b/" + rel, n=3))
    added = [l for l in diff if l.startswith("+") and not l.startswith("+++")]
    if not any(MARKER.search(l) for l in added): drift.append(rel); continue
    dst = os.path.join(PATCHES, rel + ".patch"); os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, "w", encoding="utf-8", newline="\n") as fh:
        for l in diff:
            fh.write(l if l.endswith("\n") else l + "\n\\ No newline at end of file\n")
    ported.append(rel)
print("=== PORT EDITS (patched):", len(ported), "===")
for r in ported: print("  P", r)
print("=== UPSTREAM DRIFT (take main, no patch):", len(drift), "===")
for r in drift: print("  D", r)
print("=== BINARY (skip):", len(binary), "===")
for r in binary: print("  B", r)