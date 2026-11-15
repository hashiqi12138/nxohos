/*
 * SaveSelfTest.ets - the save container's round trip, run inside the app (P2.3).
 *
 * The picker is a system surface that uitest cannot drive reliably on this device (it stops on the location
 * chooser), so the export/import pair is proven here instead: pack the live save, unpack it into a scratch
 * directory, and compare every file's bytes. That is the part that can silently corrupt a playthrough; the
 * picker around it only decides where the bytes go.
 *
 * Reached from the settings page's version row (five taps), so it costs no UI on the product path.
 */

import { fileIo as fs } from '@kit.CoreFileKit';
import { packSave, importSave, readSaveHeader, saveDir, backupDir, saveSize, SaveInfo, SaveProgress } from './SaveData';

/** What the round trip found. */
export interface SelfTestResult {
  ok: boolean;
  /** One line per check, in order. */
  lines: string[];
}

/** List a tree's files as rel -> size, for comparison. */
function treeMap(root: string, rel: string, out: Map<string, number>): void {
  let names: string[] = [];
  try {
    names = fs.listFileSync(rel.length > 0 ? `${root}/${rel}` : root);
  } catch (e) {
    return;
  }
  for (const n of names) {
    const childRel: string = rel.length > 0 ? `${rel}/${n}` : n;
    let isDir: boolean = false;
    try {
      isDir = fs.statSync(`${root}/${childRel}`).isDirectory();
    } catch (e) {
      continue;
    }
    if (isDir) treeMap(root, childRel, out);
    else {
      try {
        out.set(childRel, fs.statSync(`${root}/${childRel}`).size);
      } catch (e) {
        // gone
      }
    }
  }
}

/** Read a file whole, for a byte comparison. */
function readAll(path: string): Uint8Array {
  const st = fs.statSync(path);
  const buf: ArrayBuffer = new ArrayBuffer(st.size);
  const f = fs.openSync(path, fs.OpenMode.READ_ONLY);
  try {
    fs.readSync(f.fd, buf, { offset: 0, length: st.size });
  } finally {
    fs.closeSync(f);
  }
  return new Uint8Array(buf);
}

/**
 * Pack one game's save, unpack it elsewhere, and compare.
 *
 * The scratch directory is under files/save/ so the app's own permissions apply; it is removed at the end.
 */
export function runSaveSelfTest(filesDir: string, titleId: string): SelfTestResult {
  const lines: string[] = [];
  const src: string = saveDir(filesDir, titleId);
  const packPath: string = `${filesDir}/save/.selftest.nxsave`;
  const dest: string = `${filesDir}/save/.selftest-out`;
  const destBackup: string = `${filesDir}/save/.selftest-bak`;
  const ok = (s: string): void => { lines.push(`ok   ${s}`); };
  const bad = (s: string): void => { lines.push(`FAIL ${s}`); };

  try {
    // ---- 1. the source has something ----
    const before: Map<string, number> = new Map();
    treeMap(src, '', before);
    if (before.size === 0) {
      bad('the save directory is empty: play the game once first');
      return { ok: false, lines: lines };
    }
    ok(`source: ${before.size} file(s), ${saveSize(src)} bytes`);

    // ---- 2. pack ----
    const noop: (p: SaveProgress) => void = (_p: SaveProgress): void => {};
    const n: number = packSave(src, packPath, titleId, noop);
    const packedSize: number = fs.statSync(packPath).size;
    if (n !== before.size) bad(`pack wrote ${n} entries, expected ${before.size}`);
    else ok(`pack: ${n} entries, ${packedSize} bytes on disk`);

    // ---- 3. the header reads back ----
    const info: SaveInfo = readSaveHeader(packPath);
    if (info.title !== titleId) bad(`header title is "${info.title}", expected "${titleId}"`);
    else ok(`header: version ${info.version}, title ${info.title}, tar ${info.tarSize} bytes`);
    if (packedSize !== 48 + info.tarSize) bad(`size ${packedSize} != 48 + tar ${info.tarSize}`);
    else ok('header: the declared tar size matches the file length');

    // ---- 4. unpack into a scratch directory ----
    const back = importSave(packPath, dest, titleId, destBackup, noop);
    const after: Map<string, number> = new Map();
    treeMap(dest, '', after);
    if (after.size !== before.size) bad(`unpack produced ${after.size} files, expected ${before.size}`);
    else ok(`unpack: ${back} entries restored`);

    // ---- 5. every file matches byte for byte ----
    let mismatched: number = 0;
    // ArkTS rejects destructuring declarations, so the map is walked as key/value pairs.
    const keys: string[] = Array.from(before.keys());
    for (const rel of keys) {
      const size: number = before.get(rel) as number;
      const got: number | undefined = after.get(rel);
      if (got === undefined) { mismatched++; bad(`missing after unpack: ${rel}`); continue; }
      if (got !== size) { mismatched++; bad(`size differs: ${rel} ${size} -> ${got}`); continue; }
      const a: Uint8Array = readAll(`${src}/${rel}`);
      const b: Uint8Array = readAll(`${dest}/${rel}`);
      let same: boolean = a.length === b.length;
      if (same) {
        for (let i = 0; i < a.length; i++) {
          if (a[i] !== b[i]) { same = false; break; }
        }
      }
      if (!same) { mismatched++; bad(`bytes differ: ${rel}`); }
    }
    if (mismatched === 0) ok(`all ${before.size} file(s) match byte for byte`);

    return { ok: mismatched === 0 && lines.every((l: string): boolean => !l.startsWith('FAIL')), lines: lines };
  } catch (e) {
    const why: string = e instanceof Error ? e.message : JSON.stringify(e);
    bad(`threw: ${why}`);
    return { ok: false, lines: lines };
  } finally {
    // ---- clean up the scratch files, always ----
    try { fs.unlinkSync(packPath); } catch (e) { /* gone */ }
    try { fs.rmdirSync(dest); } catch (e) { /* not empty or gone */ }
    try { fs.rmdirSync(destBackup); } catch (e) { /* not empty or gone */ }
  }
}
