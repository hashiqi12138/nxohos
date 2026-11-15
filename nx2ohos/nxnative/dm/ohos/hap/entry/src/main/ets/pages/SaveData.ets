/*
 * SaveData.ets - export and import one game's save as a single file (P2.3).
 *
 * A save lives in files/save/<title id>/ and the native layer mounts it transactionally (work directory, swapped
 * in on Commit), so nothing outside the app may read it: the picker is the only way in or out.
 *
 * The container is deliberately the same shape as P2.1's .nxpk - a small fixed header framing a standard ustar
 * stream - because that format is already proven on this device and the unpack loop is the same code.
 *
 * Import always backs up the current save first. A player who picks the wrong file must be able to get back, and
 * that backup is the only thing standing between a mis-tap and a lost playthrough.
 */

import { fileIo as fs } from '@kit.CoreFileKit';

/**
 * The container's magic: eight bytes, so a wrong file is rejected before any work.
 *
 * It carries the two NULs explicitly. The writer zero-fills them anyway (charCodeAt past the end is NaN, which
 * a Uint8Array stores as 0), but the reader compares all eight bytes, so a six-character constant made every
 * file the app wrote fail its own check - "not a .nxsave file" with the bytes plainly reading NXSAVE.
 */
const MAGIC: string = 'NXSAVE\u0000\u0000';

/** Header layout: magic 8, version 1, reserved 7, title 16, created 8, tar size 8 = 48 bytes. */
export const SAVE_HEADER: number = 48;

/** What a .nxsave file says about itself. */
export interface SaveInfo {
  version: number;
  /** The title id the save belongs to, as ASCII. */
  title: string;
  /** Unix seconds the file was written. */
  created: number;
  /** Bytes of the tar stream that follows the header. */
  tarSize: number;
}

/** One file's worth of progress, for the caller's caption. */
export interface SaveProgress {
  files: number;
  bytes: number;
  currentName: string;
}

/** The directory holding one game's save. */
export function saveDir(filesDir: string, titleId: string): string {
  return `${filesDir}/save/${titleId}`;
}

/** The backup the import writes before overwriting; one per game, replaced each time. */
export function backupDir(filesDir: string, titleId: string): string {
  return `${filesDir}/save/${titleId}.backup`;
}

/** Read a NUL-terminated ASCII field from a 512-byte ustar header. */
function tarString(h: Uint8Array, off: number, len: number): string {
  let end: number = off;
  while (end < off + len && h[end] !== 0) end++;
  let s: string = '';
  for (let i = off; i < end; i++) s += String.fromCharCode(h[i]);
  return s;
}

/** Read an octal field from a ustar header. */
function tarOctal(h: Uint8Array, off: number, len: number): number {
  let v: number = 0;
  for (let i = off; i < off + len; i++) {
    const c: number = h[i];
    if (c === 0 || c === 32) continue;
    if (c < 48 || c > 55) break;
    v = v * 8 + (c - 48);
  }
  return v;
}

/** Read a .nxsave header from a file descriptor at offset 0. */
export function readSaveHeader(uri: string): SaveInfo {
  const f = fs.openSync(uri, fs.OpenMode.READ_ONLY);
  try {
    const buf: ArrayBuffer = new ArrayBuffer(SAVE_HEADER);
    fs.readSync(f.fd, buf, { offset: 0, length: SAVE_HEADER });
    const b: Uint8Array = new Uint8Array(buf);
    let magic: string = '';
    for (let i = 0; i < 8; i++) magic += String.fromCharCode(b[i]);
    if (magic !== MAGIC) {
      // Name what was actually there: a byte dump turns a bare rejection into a diagnosis.
      let hex: string = '';
      for (let i = 0; i < 16; i++) hex += (b[i] < 16 ? '0' : '') + b[i].toString(16);
      throw new Error('not a .nxsave file (first bytes: ' + hex + ', magic was ' + JSON.stringify(magic) + ')');
    }
    return { version: b[8], title: readFixed(b, 16, 16), created: readU64(b, 32), tarSize: readU64(b, 40) };
  } finally {
    fs.closeSync(f);
  }
}

/** Write a NUL-terminated ASCII field into a ustar header. */
function putString(h: Uint8Array, off: number, len: number, s: string): void {
  const n: number = Math.min(s.length, len - 1);
  for (let i = 0; i < n; i++) h[off + i] = s.charCodeAt(i) & 0xff;
}

/**
 * Write a 16-byte ASCII field with no NUL terminator.
 *
 * A title id is exactly 16 characters, so the NUL-terminating helper above would truncate it to 15 (measured:
 * "01007EF00011E00"). ustar's own name field keeps its terminator, so only this one uses the full width.
 */
function putFixed(h: Uint8Array, off: number, len: number, s: string): void {
  for (let i = 0; i < len; i++) h[off + i] = i < s.length ? (s.charCodeAt(i) & 0xff) : 0;
}

/** Read a fixed-width ASCII field that may have no terminator. */
function readFixed(b: Uint8Array, off: number, len: number): string {
  let s: string = '';
  for (let i = 0; i < len; i++) {
    const c: number = b[off + i];
    if (c === 0) break;
    s += String.fromCharCode(c);
  }
  return s;
}

/** Write a little-endian u64. Math.floor first: Date.now() is a float and a shift on it is garbage. */
function putU64(h: Uint8Array, off: number, v: number): void {
  let n: number = Math.floor(v);
  for (let i = 0; i < 8; i++) {
    h[off + i] = n % 256;
    n = Math.floor(n / 256);
  }
}

/** Read a little-endian u64. */
function readU64(b: Uint8Array, off: number): number {
  let v: number = 0;
  let mul: number = 1;
  for (let i = 0; i < 8; i++) {
    v += b[off + i] * mul;
    mul *= 256;
  }
  return v;
}

/** Write a number as a zero-padded octal field, as ustar wants. */
function putOctal(h: Uint8Array, off: number, len: number, v: number): void {
  const s: string = v.toString(8);
  const pad: number = len - 1 - s.length;
  for (let i = 0; i < pad; i++) h[off + i] = 48;          // '0'
  for (let i = 0; i < s.length; i++) h[off + pad + i] = s.charCodeAt(i);
  h[off + len - 1] = 0;
}

/** A 512-byte ustar header for one file. */
function tarHeader(rel: string, size: number, mtime: number): Uint8Array {
  const h: Uint8Array = new Uint8Array(512);
  putString(h, 0, 100, rel);
  putOctal(h, 100, 8, 0o644);          // mode
  putOctal(h, 108, 8, 0);              // uid
  putOctal(h, 116, 8, 0);              // gid
  putOctal(h, 124, 12, size);
  putOctal(h, 136, 12, mtime);
  h[156] = 48;                         // typeflag '0': a regular file
  putString(h, 257, 6, 'ustar');       // magic
  h[262] = 48;                         // version "00"
  // The checksum: spaces while summing, then the octal value in its field.
  for (let i = 148; i < 156; i++) h[i] = 32;
  let sum: number = 0;
  for (let i = 0; i < 512; i++) sum += h[i];
  putOctal(h, 148, 8, sum);
  return h;
}

/** Every file under root, as paths relative to it, sorted so the archive is reproducible. */
function listFiles(root: string, rel: string, out: string[]): void {
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
    if (isDir) listFiles(root, childRel, out);
    else out.push(childRel);
  }
}

/**
 * Pack one game's save into a .nxsave at the destination the picker returned.
 *
 * The walk is bounded by the save's own size (a few MB, a handful of files), so it runs in one pass without the
 * yielding the 41k-file game walk needs.
 */
export function packSave(saveRoot: string, destUri: string, titleId: string,
                         onProgress: (p: SaveProgress) => void): number {
  const rels: string[] = [];
  listFiles(saveRoot, '', rels);
  rels.sort();

  // Sizes first: the header needs the total tar length before the first byte goes out.
  const sizes: number[] = [];
  let tarSize: number = 0;
  for (const rel of rels) {
    let sz: number = 0;
    try {
      sz = fs.statSync(`${saveRoot}/${rel}`).size;
    } catch (e) {
      sz = 0;
    }
    sizes.push(sz);
    tarSize += 512 + Math.ceil(sz / 512) * 512;
  }
  tarSize += 1024;   // two zero blocks end a ustar stream

  const out = fs.openSync(destUri, fs.OpenMode.READ_WRITE | fs.OpenMode.CREATE | fs.OpenMode.TRUNC);
  try {
    // ---- the 48-byte header ----
    const head: Uint8Array = new Uint8Array(SAVE_HEADER);
    for (let i = 0; i < 8; i++) head[i] = MAGIC.charCodeAt(i);
    head[8] = 1;                                        // version
    putFixed(head, 16, 16, titleId);
    const now: number = Math.floor(Date.now() / 1000);
    putU64(head, 32, now);
    putU64(head, 40, tarSize);
    fs.writeSync(out.fd, head.buffer);

    // ---- the tar stream ----
    let files: number = 0;
    let bytes: number = 0;
    const chunk: Uint8Array = new Uint8Array(1 << 20);
    for (let k = 0; k < rels.length; k++) {
      const rel: string = rels[k];
      const size: number = sizes[k];
      let mtime: number = now;
      try {
        mtime = Math.floor(fs.statSync(`${saveRoot}/${rel}`).mtime / 1000);
      } catch (e) {
        mtime = now;
      }
      fs.writeSync(out.fd, tarHeader(rel, size, mtime).buffer);
      const src = fs.openSync(`${saveRoot}/${rel}`, fs.OpenMode.READ_ONLY);
      try {
        let left: number = size;
        let pos: number = 0;
        while (left > 0) {
          const want: number = Math.min(left, chunk.length);
          const got: number = fs.readSync(src.fd, chunk.buffer, { offset: pos, length: want });
          if (got <= 0) break;
          fs.writeSync(out.fd, chunk.buffer.slice(0, got));
          pos += got;
          left -= got;
        }
        // pad to the next 512 boundary
        const pad: number = (512 - (size % 512)) % 512;
        if (pad > 0) fs.writeSync(out.fd, new Uint8Array(pad).buffer);
      } finally {
        fs.closeSync(src);
      }
      files++;
      bytes += size;
      onProgress({ files: files, bytes: bytes, currentName: rel });
    }
    // ---- the end-of-archive marker: two zero blocks ----
    fs.writeSync(out.fd, new Uint8Array(1024).buffer);
    return files;
  } finally {
    fs.closeSync(out);
  }
}

/** Remove a tree, tolerating a missing one. */
function removeTree(path: string): void {
  let names: string[] = [];
  try {
    names = fs.listFileSync(path);
  } catch (e) {
    return;
  }
  for (const n of names) {
    const p: string = `${path}/${n}`;
    try {
      if (fs.statSync(p).isDirectory()) removeTree(p);
      else fs.unlinkSync(p);
    } catch (e) {
      // a file that will not go is reported by the caller's own check
    }
  }
  try {
    fs.rmdirSync(path);
  } catch (e) {
    // already gone
  }
}

/** Copy a tree, so a backup is a real second copy and not a link the import could write through. */
function copyTree(src: string, dst: string): void {
  try {
    fs.mkdirSync(dst);
  } catch (e) {
    // exists
  }
  let names: string[] = [];
  try {
    names = fs.listFileSync(src);
  } catch (e) {
    return;
  }
  for (const n of names) {
    const s: string = `${src}/${n}`;
    const d: string = `${dst}/${n}`;
    let isDir: boolean = false;
    try {
      isDir = fs.statSync(s).isDirectory();
    } catch (e) {
      continue;
    }
    if (isDir) copyTree(s, d);
    else {
      try {
        fs.copyFileSync(s, d);
      } catch (e) {
        // a file that will not copy leaves the backup incomplete; the caller is told by the count
      }
    }
  }
}

/**
 * Replace one game's save with the contents of a .nxsave, keeping the previous save as a backup.
 *
 * Order matters: the backup is made before anything under the save directory is touched, so a failure part way
 * through still leaves the player their old progress.
 */
export function importSave(uri: string, saveRoot: string, titleId: string, backup: string,
                           onProgress: (p: SaveProgress) => void): number {
  const info: SaveInfo = readSaveHeader(uri);
  if (info.title !== titleId) throw new Error(`this is ${info.title}'s save, not ${titleId}'s`);

  // ---- 1. back up what is there now ----
  removeTree(backup);
  let hadSave: boolean = false;
  try {
    hadSave = fs.listFileSync(saveRoot).length > 0;
  } catch (e) {
    hadSave = false;
  }
  if (hadSave) copyTree(saveRoot, backup);

  // ---- 2. clear the live save, then unpack into it ----
  removeTree(saveRoot);
  try {
    fs.mkdirSync(saveRoot);
  } catch (e) {
    // exists
  }

  const src = fs.openSync(uri, fs.OpenMode.READ_ONLY);
  try {
    const hdr: ArrayBuffer = new ArrayBuffer(512);
    const u8: Uint8Array = new Uint8Array(hdr);
    let pos: number = SAVE_HEADER;
    let files: number = 0;
    let bytes: number = 0;
    const chunk: Uint8Array = new Uint8Array(1 << 20);
    for (;;) {
      const got: number = fs.readSync(src.fd, hdr, { offset: pos, length: 512 });
      if (got < 512) break;
      pos += 512;
      // a zero block is the end-of-archive marker
      let allZero: boolean = true;
      for (let i = 0; i < 512; i++) {
        if (u8[i] !== 0) { allZero = false; break; }
      }
      if (allZero) break;
      const name: string = tarString(u8, 0, 100);
      if (name.length === 0) break;
      const size: number = tarOctal(u8, 124, 12);
      const target: string = `${saveRoot}/${name}`;
      const slash: number = target.lastIndexOf('/');
      if (slash > 0) {
        try {
          fs.mkdirSync(target.substring(0, slash));
        } catch (e) {
          // exists
        }
      }
      const out = fs.openSync(target, fs.OpenMode.READ_WRITE | fs.OpenMode.CREATE | fs.OpenMode.TRUNC);
      try {
        let left: number = size;
        let at: number = pos;
        while (left > 0) {
          const want: number = Math.min(left, chunk.length);
          const n: number = fs.readSync(src.fd, chunk.buffer, { offset: at, length: want });
          if (n <= 0) break;
          fs.writeSync(out.fd, chunk.buffer.slice(0, n));
          at += n;
          left -= n;
        }
      } finally {
        fs.closeSync(out);
      }
      pos += Math.ceil(size / 512) * 512;
      files++;
      bytes += size;
      onProgress({ files: files, bytes: bytes, currentName: name });
    }
    return files;
  } finally {
    fs.closeSync(src);
  }
}

/** Bytes one game's save occupies, for the settings list. */
export function saveSize(saveRoot: string): number {
  let total: number = 0;
  const rels: string[] = [];
  listFiles(saveRoot, '', rels);
  for (const rel of rels) {
    try {
      total += fs.statSync(`${saveRoot}/${rel}`).size;
    } catch (e) {
      // gone
    }
  }
  return total;
}

/** Whether the backup from an earlier import still exists. */
export function hasBackup(backup: string): boolean {
  try {
    return fs.listFileSync(backup).length > 0;
  } catch (e) {
    return false;
  }
}

/** One game's save, as the settings list shows it. */
export interface SaveEntry {
  titleId: string;
  name: string;
  bytes: number;
  files: number;
  /** True when an import left a backup that could be restored. */
  hasBackup: boolean;
}

/**
 * Every game that has a save on disk.
 *
 * Reads the save directory rather than the game list: a game whose data was deleted keeps its save, and a player
 * looking for a backup wants to see it there. Names come from the caller, which knows the display names.
 */
export function listSaves(filesDir: string, nameOf: (titleId: string) => string): SaveEntry[] {
  const base: string = `${filesDir}/save`;
  let names: string[] = [];
  try {
    names = fs.listFileSync(base);
  } catch (e) {
    return [];
  }
  const out: SaveEntry[] = [];
  for (const n of names) {
    if (!/^[0-9A-Fa-f]{16}$/.test(n)) continue;
    const root: string = `${base}/${n}`;
    let isDir: boolean = false;
    try {
      isDir = fs.statSync(root).isDirectory();
    } catch (e) {
      isDir = false;
    }
    if (!isDir) continue;
    const rels: string[] = [];
    listFiles(root, '', rels);
    let bytes: number = 0;
    for (const rel of rels) {
      try {
        bytes += fs.statSync(`${root}/${rel}`).size;
      } catch (e) {
        // gone
      }
    }
    out.push({
      titleId: n,
      name: nameOf(n),
      bytes: bytes,
      files: rels.length,
      hasBackup: hasBackup(`${base}/${n}.backup`)
    });
  }
  out.sort((a: SaveEntry, b: SaveEntry): number => a.name < b.name ? -1 : (a.name > b.name ? 1 : 0));
  return out;
}

/** Restore a game's save from the backup an earlier import left, and report whether there was one. */
export function restoreBackup(filesDir: string, titleId: string): boolean {
  const live: string = saveDir(filesDir, titleId);
  const backup: string = backupDir(filesDir, titleId);
  if (!hasBackup(backup)) return false;
  removeTree(live);
  copyTree(backup, live);
  return true;
}
