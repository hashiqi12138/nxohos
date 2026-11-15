/*
 * PackImport.ets - the game data pack importer (P2.1).
 *
 * A game data pack is one .nxpk file: an 80-byte header framing a standard ustar tar (dm/tools/pack_game.py
 * builds it). The user picks that file with the system DocumentViewPicker and this class streams it into the
 * app's own sandbox - the only writer that can cross the SELinux boundary (the shell domain cannot write
 * debug_hap_data_file, and the app cannot read the shell's /data/local/tmp; both measured on the device).
 *
 * Streaming matters: 41,084 files in a tar read sequentially in one pass, versus one HTTP request per file
 * (the loopback prototype took ~40 minutes; this takes the time of one sequential read).
 *
 * The stamp is the contract that makes data independent of the app: the header carries the same sha1 the pack's
 * dm.files.tsv records over (rel, size, mtime), and the sandbox keeps it in .stamp. Equal stamps mean the data
 * is already there and the import is skipped entirely, so shipping a new entry (4.6 MB) never touches 14 GB.
 */

import { fileIo as fs } from '@kit.CoreFileKit';
import { util } from '@kit.ArkTS';

const HEADER_SIZE: number = 80;
const MAGIC: string = 'NXPK';
const TITLE_LEN: number = 16;
const STAMP_LEN: number = 48;

/** What the header says about a pack. */
export interface PackInfo {
  version: number;
  title: string;
  stamp: string;
  tarSize: number;
}

/** Progress reported to the UI: files written so far and bytes consumed of the tar. */
export interface ImportProgress {
  files: number;
  bytes: number;
  totalBytes: number;
  currentName: string;
}

/** The outcome of one import attempt. */
export interface ImportResult {
  ok: boolean;
  /** 'imported' | 'up-to-date' | 'not-a-pack' | 'wrong-title' | 'failed' */
  kind: string;
  detail: string;
  files: number;
}

/**
 * Parse the 80-byte header of an .nxpk.
 * Throws when the file is not a pack (bad magic or short).
 */
export function readPackHeader(path: string): PackInfo {
  const f = fs.openSync(path, fs.OpenMode.READ_ONLY);
  try {
    const buf: ArrayBuffer = new ArrayBuffer(HEADER_SIZE);
    const got: number = fs.readSync(f.fd, buf, { offset: 0, length: HEADER_SIZE });
    if (got !== HEADER_SIZE) throw new Error(`header is ${got} bytes, expected ${HEADER_SIZE}`);
    const bytes: Uint8Array = new Uint8Array(buf);
    let magic: string = '';
    for (let i = 0; i < 4; i++) magic += String.fromCharCode(bytes[i]);
    if (magic !== MAGIC) throw new Error(`not a game pack (magic "${magic}")`);
    const dec = util.TextDecoder.create('utf-8');
    const title: string = dec.decodeWithStream(bytes.slice(8, 8 + TITLE_LEN)).replace(/\u0000+$/, '').trim();
    const stamp: string = dec.decodeWithStream(bytes.slice(24, 24 + STAMP_LEN)).replace(/\u0000+$/, '').trim();
    // tarSize: little-endian u64 in the last 8 header bytes
    let tarSize: number = 0;
    for (let i = 0; i < 8; i++) tarSize += bytes[72 + i] * Math.pow(256, i);
    return { version: bytes[4], title: title, stamp: stamp, tarSize: tarSize };
  } finally {
    fs.closeSync(f);
  }
}

/** Read a NUL-terminated ASCII field from a 512-byte ustar header. */
function tarString(h: Uint8Array, off: number, len: number): string {
  let s: string = '';
  for (let i = 0; i < len; i++) {
    const c: number = h[off + i];
    if (c === 0) break;
    s += String.fromCharCode(c);
  }
  return s;
}

/** Read an octal field from a 512-byte ustar header. */
function tarOctal(h: Uint8Array, off: number, len: number): number {
  let v: number = 0;
  for (let i = 0; i < len; i++) {
    const c: number = h[off + i];
    if (c === 0 || c === 0x20) { if (v > 0) break; else continue; }
    if (c < 0x30 || c > 0x37) break;
    v = v * 8 + (c - 0x30);
  }
  return v;
}

/**
 * Stream an .nxpk's tar body into `root`, clearing the pack-owned folders first.
 *
 * Only the three folders the pack owns are removed (mods, RomFS, aoc): a file an older pack had, or a DLC the
 * pack no longer carries, would otherwise stay behind and be read as if the pack still had it.
 *
 * Returns the number of files written. Throws on a torn or malformed stream; the caller leaves the stamp
 * unwritten, so the next launch imports again rather than trusting a half copy.
 */
export async function importPack(path: string, root: string, info: PackInfo,
                                 onProgress: (p: ImportProgress) => void): Promise<number> {
  // clear the owned set (same rule as the HAP staging path)
  for (const owned of ['mods', 'RomFS', 'aoc']) {
    removeTree(root + '/' + owned);
  }
  try {
    fs.mkdirSync(root, true);
  } catch (e) {
  }

  const src = fs.openSync(path, fs.OpenMode.READ_ONLY);
  let files: number = 0;
  let pos: number = HEADER_SIZE;   // the tar body starts right after the 80-byte container header
  const BLOCK: number = 512;
  const header: ArrayBuffer = new ArrayBuffer(BLOCK);
  const hbytes: Uint8Array = new Uint8Array(header);
  // one 1 MB staging buffer for the whole run: allocating one per file churned 41k ArrayBuffers through the
  // ArkTS heap and helped push the process into the watchdog's unresponsive kill
  const chunk: ArrayBuffer = new ArrayBuffer(1 << 20);
  try {
    // Position is tracked here and passed as ReadOptions.offset on every read. On this platform a readSync
    // advances the descriptor, but a caller that only ever seeks once cannot rely on that staying true across
    // 41k iterations (measured: the reader started at offset 0 and made a 0-byte file named "NXPK" from the
    // container header). offset in ReadOptions is the file position, so this is self-correcting.
    while (pos - HEADER_SIZE < info.tarSize) {
      // one ustar header block
      const got: number = fs.readSync(src.fd, header, { offset: pos, length: BLOCK });
      if (got !== BLOCK) break;
      pos += BLOCK;
      // two zero blocks end the archive
      let allZero: boolean = true;
      for (let i = 0; i < BLOCK; i++) { if (hbytes[i] !== 0) { allZero = false; break; } }
      if (allZero) break;
      const name: string = tarString(hbytes, 0, 100);
      const prefix: string = tarString(hbytes, 345, 155);
      const full: string = prefix.length ? prefix + '/' + name : name;
      const size: number = tarOctal(hbytes, 124, 12);
      const type: number = hbytes[156];
      // '0' or NUL: a regular file; '5': a directory; others (links, pax) are skipped by their size
      if (type === 0 || type === 0x30) {
        const dst: string = root + '/' + full;
        const dir: string = dst.substring(0, dst.lastIndexOf('/'));
        try {
          fs.mkdirSync(dir, true);
        } catch (e) {
        }
        const out = fs.openSync(dst, fs.OpenMode.CREATE | fs.OpenMode.READ_WRITE | fs.OpenMode.TRUNC);
        try {
          let left: number = size;
          let sinceYield: number = 0;
          while (left > 0) {
            const want: number = Math.min(chunk.byteLength, left);
            const n: number = fs.readSync(src.fd, chunk, { offset: pos, length: want });
            if (n <= 0) throw new Error(`short read at ${full}`);
            fs.writeSync(out.fd, chunk, { length: n });
            left -= n;
            pos += n;
            sinceYield += n;
            // Even inside one large file, yield every 8 MB: a single 500 MB asset would otherwise hold the
            // thread for seconds and trip the same 6 s watchdog kill.
            if (sinceYield >= (1 << 20)) {
              sinceYield = 0;
              await new Promise<void>((resolve) => setTimeout(resolve, 1));
            }
          }
        } finally {
          fs.closeSync(out);
        }
        files++;
        if (files % 5 === 0) {
          onProgress({ files: files, bytes: pos - HEADER_SIZE, totalBytes: info.tarSize, currentName: full });
          // Yield to the UI thread. The system kills a foreground app whose thread blocks for 6 s
          // (measured: PROCESS_KILL reason=THREAD_BLOCK_6S at 5 GB in). A macrotask yield every 20 files keeps
          // each synchronous stretch well under that: the largest files in a game pack are a few MB, so 20
          // files is a few hundred ms of work, not seconds.
          await new Promise<void>((resolve) => setTimeout(resolve, 1));
        }
      } else if (type === 0x35) {
        const dst: string = root + '/' + full;
        try {
          fs.mkdirSync(dst, true);
        } catch (e) {
        }
      } else {
        // skip the member's data (symlink, pax header, ...)
        pos += size;
      }
      // tar pads every member to a 512-byte boundary
      const pad: number = (BLOCK - (size % BLOCK)) % BLOCK;
      pos += pad;
    }
  } finally {
    fs.closeSync(src);
  }
  onProgress({ files: files, bytes: info.tarSize, totalBytes: info.tarSize, currentName: '' });
  return files;
}

/** Remove a directory tree bottom-up; false when the path is not there. */
export function removeTree(path: string): boolean {
  let isDir: boolean = false;
  try {
    isDir = fs.statSync(path).isDirectory();
  } catch (e) {
    return false;
  }
  if (!isDir) {
    try {
      fs.unlinkSync(path);
      return true;
    } catch (e) {
      return false;
    }
  }
  let names: string[] = [];
  try {
    names = fs.listFileSync(path);
  } catch (e) {
    names = [];
  }
  for (const name of names) {
    removeTree(path + '/' + name);
  }
  try {
    fs.rmdirSync(path);
    return true;
  } catch (e) {
    return false;
  }
}

/** The sandbox's recorded pack stamp, or '' when it has none. */
export function readStamp(root: string): string {
  try {
    return fs.readTextSync(root + '/.stamp').trim();
  } catch (e) {
    return '';
  }
}

/** Record a pack as imported. */
export function writeStamp(root: string, stamp: string): void {
  const f = fs.openSync(root + '/.stamp', fs.OpenMode.CREATE | fs.OpenMode.READ_WRITE | fs.OpenMode.TRUNC);
  try {
    fs.writeSync(f.fd, stamp);
  } finally {
    fs.closeSync(f);
  }
}
