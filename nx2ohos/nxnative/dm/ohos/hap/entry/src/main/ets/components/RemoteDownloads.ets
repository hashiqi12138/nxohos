/**
 * RemoteDownloads.ets - the download state that outlives the library page (S4).
 *
 * A 1.17 GB pack takes minutes, and a player will leave the library while it runs: into settings, into a game,
 * back out again. State held in a component's @State dies with the page, so the progress bar would restart at 0 and
 * the section would forget what it was doing. Everything a download needs lives here instead, in a module-level
 * singleton the section subscribes to; the section is a view of it, not its owner.
 *
 * The work itself is ArkTS http in this process: killing the app stops a download, and the file stays in the cache
 * directory as a .part so the next attempt resumes from it (the server answers 206 for the Range).
 */
import fs from '@ohos.file.fs';
import { RemotePack, downloadPack } from '../pages/RemoteLibrary';
import { readPackHeader, importPack, readStamp, writeStamp, PackInfo, ImportProgress } from '../pages/PackImport';
import { gamesDir } from '../pages/GameLibrary';

/** What one pack is doing. Idle covers "not started" and "stopped, resumable" - the same thing to the user. */
export enum PackState {
  Idle,
  Downloading,
  Importing,
  Installed,
  Failed
}

/** One pack's live state. */
export interface PackProgress {
  pack: RemotePack;
  state: PackState;
  got: number;        // bytes on disk
  total: number;      // the container's size, 0 when unknown
  speed: number;      // bytes a second, smoothed
  note: string;       // the failure's reason, or 'installed'
}

/** A subscriber: the section re-reads the list when one of these fires. */
type Listener = () => void;

class Registry {
  private items: PackProgress[] = [];
  private listeners: Listener[] = [];
  private source: string = '';
  private filesDir: string = '';
  private cacheDir: string = '';
  private cancelled: Set<string> = new Set();

  /** Point the registry at the current sandbox: called by the section before it lists or downloads. */
  attach(source: string, filesDir: string, cacheDir: string): void {
    this.source = source;
    this.filesDir = filesDir;
    this.cacheDir = cacheDir;
  }

  subscribe(fn: Listener): void {
    if (this.listeners.indexOf(fn) < 0) this.listeners.push(fn);
  }

  unsubscribe(fn: Listener): void {
    const i = this.listeners.indexOf(fn);
    if (i >= 0) this.listeners.splice(i, 1);
  }

  private notify(): void {
    for (const fn of this.listeners) {
      try { fn(); } catch (e) { }
    }
  }

  /** Replace the list with what the server holds, keeping the state of any pack already known here. */
  setPacks(packs: RemotePack[]): void {
    const out: PackProgress[] = [];
    for (const p of packs) {
      const known: PackProgress | undefined = this.find(p.title);
      if (known !== undefined) {
        // the same pack, refreshed: keep its progress and state, update the server's numbers
        known.pack = p;
        if (known.total === 0) known.total = p.bytes;
        out.push(known);
      } else {
        const installed: boolean = this.filesDir.length > 0
          && readStamp(gamesDir(this.filesDir) + '/' + p.title) === p.stamp;
        out.push({
          pack: p,
          state: installed ? PackState.Installed : PackState.Idle,
          got: installed ? p.bytes : this.partBytes(p),
          total: p.bytes,
          speed: 0,
          note: installed ? 'installed' : ''
        });
      }
    }
    this.items = out;
    this.notify();
  }

  list(): PackProgress[] {
    return this.items;
  }

  private find(title: string): PackProgress | undefined {
    for (const it of this.items) if (it.pack.title === title) return it;
    return undefined;
  }

  /** What a partial download already holds, so a resumed pack shows its real starting point. */
  private partBytes(p: RemotePack): number {
    if (!this.cacheDir.length) return 0;
    try {
      return fs.statSync(this.tmpPath(p)).size;
    } catch (e) {
      return 0;
    }
  }

  private tmpPath(p: RemotePack): string {
    return this.cacheDir + '/remote_' + p.title + '.nxpk';
  }

  /** True while any pack is downloading: the library shows its banner then. */
  busy(): boolean {
    for (const it of this.items) {
      if (it.state === PackState.Downloading || it.state === PackState.Importing) return true;
    }
    return false;
  }

  /** The pack being downloaded or imported, for the banner's label. */
  activeName(): string {
    for (const it of this.items) {
      if (it.state === PackState.Downloading || it.state === PackState.Importing) return it.pack.name;
    }
    return '';
  }

  /** The active pack's percentage, 0-100. */
  activePercent(): number {
    for (const it of this.items) {
      if (it.state !== PackState.Downloading && it.state !== PackState.Importing) continue;
      if (it.state === PackState.Importing) return 99;
      return it.total > 0 ? Math.min(99, Math.floor(it.got * 100 / it.total)) : 0;
    }
    return 0;
  }

  cancel(title: string): void {
    this.cancelled.add(title);
  }

  /**
   * Download one pack and import it. A pack already complete on disk skips straight to the import; one already
   * imported skips everything.
   */
  async start(title: string): Promise<void> {
    const it = this.find(title);
    if (it === undefined || it.state === PackState.Downloading || it.state === PackState.Importing) return;
    if (it.state === PackState.Installed) return;
    this.cancelled.delete(title);
    it.state = PackState.Downloading;
    it.note = '';
    it.speed = 0;
    this.notify();

    const tmp: string = this.tmpPath(it.pack);
    let lastGot: number = it.got;
    let lastAt: number = Date.now();
    try {
      await downloadPack(this.source, it.pack, tmp, (got: number, total: number) => {
        const now: number = Date.now();
        const dt: number = now - lastAt;
        if (dt >= 500) {
          // smoothed over the last interval: a per-chunk rate jumps around too much to read
          const inst: number = (got - lastGot) * 1000 / dt;
          it.speed = it.speed > 0 ? (it.speed * 0.6 + inst * 0.4) : inst;
          lastGot = got;
          lastAt = now;
        }
        it.got = got;
        if (total > 0) it.total = total;
        this.notify();
      });
      if (this.cancelled.has(title)) {
        it.state = PackState.Idle;
        it.note = '';
        this.notify();
        return;
      }
      it.state = PackState.Importing;
      it.speed = 0;
      this.notify();
      const info: PackInfo = readPackHeader(tmp);
      const dst: string = gamesDir(this.filesDir) + '/' + info.title;
      await importPack(tmp, dst, info, (p: ImportProgress) => { });
      writeStamp(dst, info.stamp);
      try { fs.unlinkSync(tmp); } catch (e) { }
      it.state = PackState.Installed;
      it.got = it.total;
      it.note = 'installed';
      this.notify();
    } catch (e) {
      it.state = this.cancelled.has(title) ? PackState.Idle : PackState.Failed;
      it.note = this.cancelled.has(title) ? '' : (e instanceof Error ? e.message : JSON.stringify(e));
      it.speed = 0;
      this.notify();
    }
  }
}

/** The one registry: every view of the download list reads this. */
export const RemoteDownloads: Registry = new Registry();

/** '2.1 MB/s' from a byte rate; '' before the first sample. */
export function speedLabel(bps: number): string {
  if (bps <= 0) return '';
  if (bps >= 1 << 20) return (bps / (1 << 20)).toFixed(1) + ' MB/s';
  if (bps >= 1 << 10) return Math.round(bps / 1024) + ' KB/s';
  return Math.round(bps) + ' B/s';
}

/** '8 分 30 秒' from the bytes left and the rate; '' when either is unknown. */
export function etaLabel(left: number, bps: number): string {
  if (bps <= 0 || left <= 0) return '';
  const s: number = Math.round(left / bps);
  if (s < 60) return s + ' 秒';
  const m: number = Math.floor(s / 60);
  if (m < 60) return m + ' 分 ' + (s % 60) + ' 秒';
  return Math.floor(m / 60) + ' 时 ' + (m % 60) + ' 分';
}

/** '1.17 GB' / '412 MB' from a byte count. */
export function sizeLabel(bytes: number): string {
  if (bytes >= 1 << 30) return (bytes / (1 << 30)).toFixed(2) + ' GB';
  if (bytes >= 1 << 20) return Math.round(bytes / (1 << 20)) + ' MB';
  return Math.round(bytes / 1024) + ' KB';
}
