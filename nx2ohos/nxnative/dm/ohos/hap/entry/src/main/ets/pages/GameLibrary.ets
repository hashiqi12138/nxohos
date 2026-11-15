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
