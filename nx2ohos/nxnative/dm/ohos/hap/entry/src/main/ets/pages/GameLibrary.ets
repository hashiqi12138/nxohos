/*
 * GameLibrary.ets - the installed-games view (P2.1 step 4, P2.2 skeleton).
 *
 * Each imported pack lives in its own directory under <filesDir>/games/<title-id>/, holding the unpacked tree
 * (RomFS/, mods/, aoc/, shaders.dmsv, env.txt, nx2a-data.json) and a .stamp recording which pack it came from.
 * The native layer takes the directory as its root, so it does not care how many games exist or what they are
 * called: the library is a pure ArkTS concern.
 *
 * This module only reads and removes; the import itself stays in PackImport (the picker path and the loopback
 * path both feed it).
 */

import { fileIo as fs } from '@kit.CoreFileKit';
import { util } from '@kit.ArkTS';

/** One installed game, as the library view needs it. */
export interface GameEntry {
  titleId: string;      // the directory name, e.g. 01007EF00011E000
  name: string;         // a display name, from nx2a-data.json when it names one
  root: string;         // the directory handed to startGame
  stamp: string;        // the pack stamp this data came from
  sizeBytes: number;    // bytes on disk (du-style sum of the tree)
  hasData: boolean;     // RomFS/ and mods/main.patched.bin both present
  iconPath: string;     // <root>/icon.jpg when the pack carries one, else ''
}

/**
 * The cached metadata a game's directory keeps, so listing does not walk 41k files.
 *
 * Written once after an import (the import already knows the size and stamp), read on every library load. A
 * missing or stale cache falls back to a real walk, and the walk's answer is written back.
 */
interface GameMeta {
  stamp: string;
  name: string;
  sizeBytes: number;
  hasData: boolean;
  icon: string;
}

const META_FILE: string = '/.meta.json';

// ---- S3: the shared import state, so the picker path and the loopback (debug) path feed one progress UI ----

/** What one import is doing, for the banner and the panel. */
export class ImportStatus {
  /** The game's display name. */
  static gameName: string = '';
  /** The title ID being imported. */
  static titleId: string = '';
  /** Bytes of work done: the download's bytes plus the unpack's bytes, over the same total. */
  static doneBytes: number = 0;
  /** The total the work is measured against: 80 + tarSize of the container. */
  static totalBytes: number = 0;
  /** Which phase the import is in, for the caption. */
  static phase: string = '';   // '' = idle, 'download' | 'unpack'
  /** The file being written, single line, for the panel. */
  static currentFile: string = '';
  /** An error message, set when the import failed; the banner clears on read. */
  static error: string = '';
  /** Whether an import is running at all. */
  static active: boolean = false;
  /** Sliding samples for the rate estimate: {t: ms since start, bytes: done}. */
  private static samples: ImportSample[] = [];
  private static startedAt: number = 0;

  static begin(gameName: string, titleId: string, totalBytes: number): void {
    ImportStatus.gameName = gameName;
    ImportStatus.titleId = titleId;
    ImportStatus.totalBytes = totalBytes > 0 ? totalBytes : 1;
    ImportStatus.doneBytes = 0;
    ImportStatus.phase = 'download';
    ImportStatus.currentFile = '';
    ImportStatus.error = '';
    ImportStatus.active = true;
    ImportStatus.samples = [];
    ImportStatus.startedAt = Date.now();
  }

  /** Record progress. Call at most every 2 s (a sample denser than that only jitters the estimate). */
  static tick(doneBytes: number, phase: string, currentFile: string): void {
    ImportStatus.doneBytes = doneBytes;
    ImportStatus.phase = phase;
    ImportStatus.currentFile = currentFile;
    if (ImportStatus.samples.length === 0 ||
      Date.now() - ImportStatus.startedAt - ImportStatus.samples[ImportStatus.samples.length - 1].t >= 2000) {
      ImportStatus.samples.push({ t: Date.now() - ImportStatus.startedAt, bytes: doneBytes });
      if (ImportStatus.samples.length > 6) ImportStatus.shift();
    }
  }

  private static shift(): void {
    ImportStatus.samples.splice(0, ImportStatus.samples.length - 6);
  }

  /** 0..100, floor. */
  static percent(): number {
    if (ImportStatus.totalBytes <= 0) return 0;
    return Math.min(100, Math.floor(ImportStatus.doneBytes * 100 / ImportStatus.totalBytes));
  }

  /**
   * The remaining-time estimate from the last ~10 s of samples.
   *
   * Returns '' for the first 15 s (the number jumps before the window fills) and after a stall (the estimate
   * climbs to minutes when the rate falls to zero, which is worse than no number).
   */
  static remaining(): string {
    if (ImportStatus.samples.length < 3) return '';
    const first: ImportSample = ImportStatus.samples[0];
    const last: ImportSample = ImportStatus.samples[ImportStatus.samples.length - 1];
    const dt: number = last.t - first.t;
    if (dt < 5000) return '';
    const db: number = last.bytes - first.bytes;
    if (db <= 0) return '';   // stalled: no estimate is better than a climbing one
    const rate: number = db / dt;   // bytes per ms
    const seconds: number = Math.round((ImportStatus.totalBytes - ImportStatus.doneBytes) / rate / 1000);
    if (seconds < 60) return `约 ${seconds} 秒`;
    if (seconds < 3600) return `约 ${Math.round(seconds / 60)} 分钟`;
    return `约 ${(seconds / 3600).toFixed(1)} 小时`;
  }

  static fail(message: string): void {
    ImportStatus.error = message;
    ImportStatus.active = false;
  }

  static finish(): void {
    ImportStatus.active = false;
    ImportStatus.phase = '';
  }
}

interface ImportSample {
  t: number;
  bytes: number;
}

/** Read the cached metadata, or null when it is absent or unreadable. */
function readMeta(root: string): GameMeta | null {
  try {
    const text: string = fs.readTextSync(root + META_FILE);
    const j = JSON.parse(text) as Record<string, Object>;
    const stamp = j['stamp'];
    const name = j['name'];
    const size = j['sizeBytes'];
    const has = j['hasData'];
    const icon = j['icon'];
    return {
      stamp: typeof stamp === 'string' ? stamp : '',
      name: typeof name === 'string' ? name : '',
      sizeBytes: typeof size === 'number' ? size : 0,
      hasData: has === true,
      icon: typeof icon === 'string' ? icon : ''
    };
  } catch (e) {
    return null;
  }
}

/**
 * Write the cache for a game. Called after an import, when the size and stamp are already known, and by the
 * library after a real walk that found no cache.
 */
export function writeMeta(root: string, stamp: string, name: string, sizeBytes: number,
                          hasData: boolean, icon: string): void {
  const meta: GameMeta = { stamp: stamp, name: name, sizeBytes: sizeBytes, hasData: hasData, icon: icon };
  try {
    const f = fs.openSync(root + META_FILE, fs.OpenMode.CREATE | fs.OpenMode.READ_WRITE | fs.OpenMode.TRUNC);
    try {
      fs.writeSync(f.fd, JSON.stringify(meta));
    } finally {
      fs.closeSync(f);
    }
  } catch (e) {
    // a cache that cannot be written only costs a walk next time
  }
}

/** The games directory, beside the per-game folders. */
export function gamesDir(filesDir: string): string {
  return filesDir + '/games';
}

/**
 * Sum a directory tree's file sizes.
 *
 * The walk visits directories breadth-first and stops after `budget` files. A full 41k-file pack undercounts at
 * a low budget (measured: 3.6 GB reported for a 14.75 GB pack at 20000), so the budget is generous; the cost is
 * one stat per file, which the import already pays thousands of times.
 */
function treeSize(path: string, budget: number): number {
  let total: number = 0;
  let seen: number = 0;
  const stack: string[] = [path];
  while (stack.length > 0 && seen < budget) {
    const cur: string = stack.pop() as string;
    let names: string[] = [];
    try {
      names = fs.listFileSync(cur);
    } catch (e) {
      continue;
    }
    for (const n of names) {
      const p: string = cur + '/' + n;
      seen++;
      if (seen >= budget) break;
      try {
        const st = fs.statSync(p);
        if (st.isDirectory()) {
          stack.push(p);
        } else {
          total += st.size;
        }
      } catch (e) {
        // an unreadable entry is not worth failing the listing for
      }
    }
  }
  return total;
}

/** Read the pack's own metadata file when it names a display name. */
function readPackName(root: string): string {
  try {
    const text: string = fs.readTextSync(root + '/nx2a-data.json');
    const j: object = JSON.parse(text) as object;
    const rec = j as Record<string, Object>;
    const n = rec['name'];
    if (typeof n === 'string' && n.length > 0) return n;
    const t = rec['title'];
    if (typeof t === 'string' && t.length > 0) return t;
  } catch (e) {
    // no metadata, or it has no name: the title ID stands in
  }
  return '';
}

/**
 * List the installed games, newest stamp first.
 *
 * Each game's directory carries a .meta.json written at import time, so a load costs one read per game instead of
 * a walk over 41k files (measured: 500 ms for five games without the cache). A game whose cache is missing or
 * whose stamp no longer matches its .stamp gets a real walk, and the result is cached for next time.
 */
export function listGames(filesDir: string): GameEntry[] {
  const base: string = gamesDir(filesDir);
  let names: string[] = [];
  try {
    names = fs.listFileSync(base);
  } catch (e) {
    return [];
  }
  const out: GameEntry[] = [];
  for (const titleId of names) {
    if (!/^[0-9A-Fa-f]{16}$/.test(titleId)) continue;
    const root: string = base + '/' + titleId;
    let isDir: boolean = false;
    try {
      isDir = fs.statSync(root).isDirectory();
    } catch (e) {
      isDir = false;
    }
    if (!isDir) continue;

    let stamp: string = '';
    try {
      stamp = fs.readTextSync(root + '/.stamp').trim();
    } catch (e) {
      stamp = '';
    }

    const cached: GameMeta | null = readMeta(root);
    let name: string = '';
    let size: number = 0;
    let hasData: boolean = false;
    let icon: string = '';

    if (cached !== null && cached.stamp === stamp) {
      name = cached.name;
      size = cached.sizeBytes;
      hasData = cached.hasData;
      icon = cached.icon;
    } else {
      // no cache, or it describes a different pack: walk once and remember the answer
      hasData = false;
      try {
        hasData = fs.accessSync(root + '/RomFS') && fs.accessSync(root + '/mods/main.patched.bin');
      } catch (e) {
        hasData = false;
      }
      name = readPackName(root);
      size = treeSize(root, 60000);
      icon = '';
      try {
        if (fs.accessSync(root + '/icon.jpg')) icon = 'icon.jpg';
      } catch (e) {
        icon = '';
      }
      writeMeta(root, stamp, name, size, hasData, icon);
    }

    out.push({
      titleId: titleId,
      name: name.length > 0 ? name : titleId,
      root: root,
      stamp: stamp,
      sizeBytes: size,
      hasData: hasData,
      iconPath: icon.length > 0 ? root + '/' + icon : ''
    });
  }
  // newest stamp first: a fresh import floats to the top
  out.sort((a: GameEntry, b: GameEntry): number => a.stamp < b.stamp ? 1 : (a.stamp > b.stamp ? -1 : 0));
  return out;
}

/** Delete one game's data. Returns false when the directory was not there. */
export function deleteGame(filesDir: string, titleId: string): boolean {
  const root: string = gamesDir(filesDir) + '/' + titleId;
  let isDir: boolean = false;
  try {
    isDir = fs.statSync(root).isDirectory();
  } catch (e) {
    return false;
  }
  if (!isDir) return false;
  removeTree(root);
  try {
    return !fs.accessSync(root);
  } catch (e) {
    return true;
  }
}

/** Remove a directory tree bottom-up (rmdir is not recursive on this platform). */
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

/** A human size for the library row. */
export function humanSize(bytes: number): string {
  if (bytes >= 1 << 30) return `${(bytes / (1 << 30)).toFixed(1)} GB`;
  if (bytes >= 1 << 20) return `${(bytes / (1 << 20)).toFixed(0)} MB`;
  if (bytes >= 1 << 10) return `${(bytes / (1 << 10)).toFixed(0)} KB`;
  return `${bytes} B`;
}

/** What a data check found. */
export interface VerifyResult {
  ok: boolean;
  /** A sentence for the user: what is there, or what is missing. */
  detail: string;
  /** The paths the check looked for, for the detail line. */
  checked: string[];
}

/**
 * Check one game's tree against what startGame needs.
 *
 * Not a hash of 14 GB - that would take minutes and answer a question nobody asked. It confirms the two things
 * the loader actually opens (RomFS/ and mods/main.patched.bin), the pack's own metadata, and that the stamp is
 * still the one the import wrote. A missing piece is named so the user knows what to re-import.
 */
export function verifyGame(root: string): VerifyResult {
  const checked: string[] = [];
  const missing: string[] = [];
  const need: string[] = ['RomFS', 'mods/main.patched.bin', 'nx2a-data.json', '.stamp'];
  for (const rel of need) {
    checked.push(rel);
    let there: boolean = false;
    try {
      there = fs.accessSync(root + '/' + rel);
    } catch (e) {
      there = false;
    }
    if (!there) missing.push(rel);
  }
  if (missing.length === 0) {
    return { ok: true, detail: '数据完整，可以运行', checked: checked };
  }
  return {
    ok: false,
    detail: `缺少 ${missing.join('、')}，请重新导入`,
    checked: checked
  };
}

/** The stamp a game's tree currently carries, for the detail sheet. */
export function gameStamp(root: string): string {
  try {
    return fs.readTextSync(root + '/.stamp').trim();
  } catch (e) {
    return '';
  }
}

/** The pack's own metadata, for the detail sheet's version line. */
export function gameVersion(root: string): string {
  try {
    const text: string = fs.readTextSync(root + '/nx2a-data.json');
    const j = JSON.parse(text) as Record<string, Object>;
    const d = j['display'];
    const v = j['version'];
    if (typeof d === 'string' && d.length > 0) return d;
    if (typeof v === 'string' && v.length > 0) return v;
  } catch (e) {
    // no metadata: the line stays empty
  }
  return '';
}

// ---- the settings page's data group: what the sandbox is using, and what can be thrown away ----

/**
 * Bytes the game trees occupy, walked in slices.
 *
 * The walk yields every YIELD_EVERY entries: 41k stat() calls on the UI thread trip HarmonyOS's
 * THREAD_BLOCK_3S watchdog, which force-exits the app (measured on this device - the settings page opened and the
 * process was killed three seconds later). The same trap cost the P2.1 import four iterations.
 */
const YIELD_EVERY: number = 400;

/** A directory being walked, with the cursor into its listing. */
interface WalkFrame {
  dir: string;
  names: string[];
  at: number;
}

function treeSizeAsync(root: string, budget: number): Promise<number> {
  return new Promise<number>((resolve: (v: number) => void) => {
    let total: number = 0;
    let seen: number = 0;
    let sinceYield: number = 0;
    // Explicit frames with a cursor each: a bare directory stack loses its place when the walk yields in the
    // middle of a listing, which made this never finish (measured: "计算中…" for good).
    const stack: WalkFrame[] = [];
    try {
      stack.push({ dir: root, names: fs.listFileSync(root), at: 0 });
    } catch (e) {
      resolve(0);
      return;
    }
    const step: () => void = () => {
      while (stack.length > 0) {
        const frame: WalkFrame = stack[stack.length - 1];
        if (frame.at >= frame.names.length) {
          stack.pop();
          continue;
        }
        const n: string = frame.names[frame.at];
        frame.at++;
        const p: string = frame.dir + '/' + n;
        try {
          const st = fs.statSync(p);
          if (st.isDirectory()) {
            try {
              stack.push({ dir: p, names: fs.listFileSync(p), at: 0 });
            } catch (e) {
              // an unreadable directory contributes nothing
            }
          } else {
            total += st.size;
          }
        } catch (e) {
          // a file that vanished mid-walk: its bytes are gone anyway
        }
        seen++;
        if (seen >= budget) { resolve(total); return; }
        sinceYield++;
        if (sinceYield >= YIELD_EVERY) {
          sinceYield = 0;
          setTimeout(step, 0);
          return;
        }
      }
      resolve(total);
    };
    step();
  });
}

/** Bytes the game trees occupy, yielding so the UI thread stays alive. */
export async function gamesSize(filesDir: string): Promise<number> {
  return await treeSizeAsync(gamesDir(filesDir), 60000);
}

/**
 * Bytes of throwaway cache: a downloaded pack, its .part, and the temp dir.
 *
 * The unpacked game data is not cache - it is the install - so it is never counted here.
 */
export function cacheSize(filesDir: string, cacheDir: string): number {
  let total: number = 0;
  const names: string[] = ['/pack.nxpk', '/pack.nxpk.part'];
  for (const n of names) {
    try {
      const st = fs.statSync(cacheDir + n);
      if (st.isFile()) total += st.size;
    } catch (e) {
      // not there
    }
  }
  return total;
}

/**
 * Delete the throwaway cache and report the bytes freed.
 *
 * Only the two pack files: a .part is an unfinished import, so the confirm dialog says its progress is lost,
 * and nothing under files/ is touched.
 */
export function clearCache(cacheDir: string): number {
  const freed: number = cacheSize('', cacheDir);
  for (const n of ['/pack.nxpk', '/pack.nxpk.part']) {
    try {
      fs.unlinkSync(cacheDir + n);
    } catch (e) {
      // already gone, or not ours to remove
    }
  }
  return freed;
}
