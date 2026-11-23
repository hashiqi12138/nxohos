/*
 * GameCatalog.ets - ONE game model for the library, whether the game is on this device or on the pack server.
 *
 * Today the library has two parallel shapes that never meet: GameEntry (GameLibrary.ets:17, an installed folder)
 * and RemotePack (RemoteLibrary.ets:17, five fields off /list). LibraryPage draws them in two different tabs with
 * two different row components (LibraryPage.ets:726 RemotePacksSection vs :902 grid), so "the same game" appears
 * twice with two vocabularies and neither side knows what the other knows: the remote row cannot say it is already
 * installed at this stamp, and the local card cannot say a newer pack exists.
 *
 * This module is the single shape and the single state machine. Both sources are read into GameItem; the UI
 * switches on `state`, never on where the item came from. A field that only one source has is optional and says so
 * (stamp '' = unknown, perf null = not measured), so a missing fact never becomes a false one.
 *
 * State machine (one axis, in this order of precedence):
 *
 *   Unknown ──list/scan──► Available ──download──► Downloading ──unpack──► Importing ──ok──► Installed
 *                              │                     │                      │
 *                              │                     └──cancel/err──► Available (partial bytes kept, resumable)
 *                              │                                            │
 *                              └──server stamp == installed .stamp──────────┴──► UpdateAvailable
 *                                                                                │
 *   Installed ──pack removed from server──► Installed (source stays, no update offered)
 *   Installed ──data incomplete on disk──► Broken (needs a re-import; GameLibrary.isStartable already decides)
 *
 * `UpdateAvailable` is not a second state of the install: it is Installed plus a newer stamp on the server, and
 * the card shows one badge for it. That is the whole point of one model - "installed" and "updatable" were never
 * mutually exclusive.
 */
import { fileIo as fs } from '@kit.CoreFileKit';
import { GameEntry, gamesDir, listGames, isStartable, humanSize } from './GameLibrary';
import { RemotePack, listRemote, PREF_SOURCE, normalizeSource } from './RemoteLibrary';
import {
  PerfRecord, PerfFile, readPerf, bestPerf, coverPath, perfLine, thisDevice
} from './GamePerf';

/** Where an item's bytes live. */
export enum ItemSource {
  /** Under <filesDir>/games/<titleId>/ only. */
  Local,
  /** On the pack server only. */
  Remote,
  /** Both: the installed copy and the server's copy are the same title (stamps compared). */
  Both
}

/** The one state axis. See the header for the transitions. */
export enum ItemState {
  /** Nothing is known yet (a listing is in flight). */
  Unknown,
  /** The server has it, the device does not. */
  Available,
  /** Installed, but the server's stamp is newer than the sandbox's. */
  UpdateAvailable,
  /** A download is running (bytes on disk, resumable). */
  Downloading,
  /** The container is on disk and being unpacked into games/<id>/. */
  Importing,
  /** Installed and startable: the only state whose card launches. */
  Installed,
  /** The folder is there but RomFS/ or mods/main.patched.bin is missing. */
  Broken,
  /** The last attempt failed; `note` says why. */
  Failed
}

/** One game, from either source, with the facts the UI shows. */
export interface GameItem {
  /** The join key: the 16-hex title ID. Never empty. */
  titleId: string;
  /** The display name: the pack's own name, else the file name, else the title ID. */
  name: string;
  source: ItemSource;
  state: ItemState;
  /** The installed tree's root, or '' when nothing is installed. */
  root: string;
  /** The pack stamp the installed tree carries, or '' when nothing is installed. */
  installedStamp: string;
  /** The pack stamp the server offers, or '' when the item is local-only. */
  remoteStamp: string;
  /** Bytes on disk when installed; otherwise the container's size. */
  bytes: number;
  /** '14.7 GB'. */
  sizeLabel: string;
  /** The cover to draw: the captured shot, else the pack's icon, else '' (the card's generated placeholder). */
  coverPath: string;
  /** This device's newest measurement, else the newest porter one, else null. */
  perf: PerfRecord | null;
  /** '60 fps · 16.7 ms · HBN-AL80', or '暂无性能数据'. */
  perfText: string;
  /** The pack's display version, when the installed pack names one. */
  version: string;
  /** A failure's reason, or ''. */
  note: string;
  /** Download progress 0..1 while Downloading/Importing; -1 otherwise (GameCard's `progress`). */
  progress: number;
  /** The remote URL to fetch, or '' when there is none. */
  url: string;
}

/** The one-line state text a row shows. */
export function stateLabel(s: ItemState): string {
  switch (s) {
    case ItemState.Available: return '可下载';
    case ItemState.UpdateAvailable: return '可更新';
    case ItemState.Downloading: return '下载中';
    case ItemState.Importing: return '导入中';
    case ItemState.Installed: return '已安装';
    case ItemState.Broken: return '数据不完整';
    case ItemState.Failed: return '失败';
    default: return '…';
  }
}

/** Whether the card may launch: the only state that hands a root to Index. */
export function canLaunch(it: GameItem): boolean {
  return it.state === ItemState.Installed && it.root.length > 0 && isStartable(it.root);
}

/** Whether the row offers a download (a first install or an update). */
export function canDownload(it: GameItem): boolean {
  return (it.state === ItemState.Available || it.state === ItemState.UpdateAvailable
    || it.state === ItemState.Failed) && it.url.length > 0;
}

/** A readable version for the sheet: the pack's own display string, or '' when it has none. */
function versionOf(root: string): string {
  try {
    const j = JSON.parse(fs.readTextSync(root + '/nx2a-data.json')) as Record<string, Object>;
    const d = j['display'];
    const v = j['version'];
    if (typeof d === 'string' && d.length > 0) return d;
    if (typeof v === 'string' && v.length > 0) return v;
  } catch (e) {
    // no metadata: the sheet's version line stays empty
  }
  return '';
}

/** Build one item from an installed tree. */
function fromLocal(g: GameEntry, remote: RemotePack | null): GameItem {
  const startable: boolean = isStartable(g.root);
  const perfFile: PerfFile | null = readPerf(g.root);
  const perf: PerfRecord | null = bestPerf(perfFile, thisDevice().model);
  let state: ItemState;
  if (!startable) state = ItemState.Broken;
  else if (remote !== null && remote.stamp.length > 0 && remote.stamp !== g.stamp) state = ItemState.UpdateAvailable;
  else state = ItemState.Installed;
  const cover: string = coverPath(g.root);
  return {
    titleId: g.titleId,
    name: g.name,
    source: remote !== null ? ItemSource.Both : ItemSource.Local,
    state: state,
    root: g.root,
    installedStamp: g.stamp,
    remoteStamp: remote !== null ? remote.stamp : '',
    bytes: g.sizeBytes,
    sizeLabel: humanSize(g.sizeBytes),
    coverPath: cover,
    perf: perf,
    perfText: perf !== null ? perfLine(perf) : '暂无性能数据',
    version: versionOf(g.root),
    note: startable ? '' : '缺少 RomFS 或 mods/main.patched.bin',
    progress: -1,
    url: remote !== null ? remote.url : ''
  };
}

/** Build one item from a server entry with nothing installed. */
function fromRemote(p: RemotePack): GameItem {
  const shown: string = p.name.replace(/\.nxpk$/i, '');
  return {
    titleId: p.title.toUpperCase(),
    name: shown.length > 0 ? shown : p.title,
    source: ItemSource.Remote,
    state: ItemState.Available,
    root: '',
    installedStamp: '',
    remoteStamp: p.stamp,
    bytes: p.bytes,
    sizeLabel: humanSize(p.bytes),
    // no root, so no captured shot: the server's /list carries no picture. A cover appears the moment the pack
    // is imported and the sampler takes its first shot; a remote-only row shows the card's placeholder.
    coverPath: '',
    perf: null,
    perfText: '暂无性能数据',
    version: '',
    note: '',
    progress: -1,
    url: p.url
  };
}

/**
 * The catalog from the device alone, synchronously: what the library draws on its first frame.
 *
 * listGames is sync (a .meta.json read per game, GameLibrary.ets:365) and the perf/cover reads are one small file
 * each, so the grid paints without waiting on a server that may not answer for four seconds
 * (RemoteLibrary.ets:47 connectTimeout). mergeRemote then upgrades the same items in place.
 */
export function buildLocalItems(filesDir: string): GameItem[] {
  const out: GameItem[] = [];
  for (const g of listGames(filesDir)) out.push(fromLocal(g, null));
  return out;
}

/**
 * Fold the server's list into items already built locally: same title ID becomes ONE item (Both), a server game
 * with nothing installed is appended as Available. Idempotent, so a refresh may call it again.
 */
export function mergeRemote(items: GameItem[], remote: RemotePack[]): GameItem[] {
  const byTitle: Map<string, RemotePack> = new Map<string, RemotePack>();
  for (const p of remote) byTitle.set(p.title.toUpperCase(), p);
  const out: GameItem[] = [];
  const seen: Set<string> = new Set<string>();
  for (const it of items) {
    const key: string = it.titleId.toUpperCase();
    const match: RemotePack | undefined = byTitle.get(key);
    if (match === undefined) {
      // the server no longer offers it: an installed game stays installed, a remote-only row goes away
      if (it.source === ItemSource.Remote && it.root.length === 0) continue;
      out.push(it);
      seen.add(key);
      continue;
    }
    seen.add(key);
    const state: ItemState = it.root.length === 0
      ? ItemState.Available
      : (match.stamp.length > 0 && match.stamp !== it.installedStamp ? ItemState.UpdateAvailable : ItemState.Installed);
    out.push({
      titleId: it.titleId,
      name: it.name,
      source: it.root.length > 0 ? ItemSource.Both : ItemSource.Remote,
      state: state,
      root: it.root,
      installedStamp: it.installedStamp,
      remoteStamp: match.stamp,
      bytes: it.root.length > 0 ? it.bytes : match.bytes,
      sizeLabel: it.root.length > 0 ? it.sizeLabel : humanSize(match.bytes),
      coverPath: it.coverPath,
      perf: it.perf,
      perfText: it.perfText,
      version: it.version,
      note: it.note,
      progress: -1,
      url: match.url
    });
  }
  for (const p of remote) {
    const key: string = p.title.toUpperCase();
    if (!seen.has(key)) out.push(fromRemote(p));
  }
  out.sort((a: GameItem, b: GameItem): number => {
    const la: number = a.state === ItemState.Installed || a.state === ItemState.UpdateAvailable ? 0 : 1;
    const lb: number = b.state === ItemState.Installed || b.state === ItemState.UpdateAvailable ? 0 : 1;
    if (la !== lb) return la - lb;
    return a.name < b.name ? -1 : (a.name > b.name ? 1 : 0);
  });
  return out;
}

/**
 * The catalog: every installed game, joined with the server's list by title ID.
 *
 * One pass, no two lists: an installed game the server also holds is ONE item (Both, Installed or
 * UpdateAvailable); a server game with nothing installed is ONE item (Remote, Available). Order: installed first
 * (they are what the player can play), then available, each by name.
 *
 * The server is optional. `source` '' or an unreachable server leaves the local games intact and sets
 * `remoteError`, because a library that cannot reach the LAN must still show what is installed (the same rule
 * LibraryPage.ets:127 applies to a broken folder).
 */
export interface CatalogResult {
  items: GameItem[];
  /** The server's failure sentence, or '' when it answered (or was never asked). */
  remoteError: string;
  /** True when a server list was actually read. */
  remoteOk: boolean;
}

export async function buildCatalog(filesDir: string, source: string): Promise<CatalogResult> {
  const local: GameItem[] = buildLocalItems(filesDir);
  const src: string = normalizeSource(source);
  if (src.length === 0) return { items: local, remoteError: '', remoteOk: false };
  try {
    const remote: RemotePack[] = await listRemote(src);
    return { items: mergeRemote(local, remote), remoteError: '', remoteOk: true };
  } catch (e) {
    return {
      items: local,
      remoteError: e instanceof Error ? e.message : JSON.stringify(e),
      remoteOk: false
    };
  }
}

/**
 * Re-read ONE title after an import or a delete, so a finished download does not rebuild the whole catalog.
 *
 * The row that changed is the only one whose state can differ; the rest keep their objects and their pictures,
 * which is what keeps a grid of captured covers from flickering on every progress tick.
 */
export function refreshItem(items: GameItem[], filesDir: string, titleId: string): GameItem[] {
  const key: string = titleId.toUpperCase();
  const idx: number = items.findIndex((it: GameItem): boolean => it.titleId === key);
  const games: GameEntry[] = listGames(filesDir);
  const g: GameEntry | undefined = games.find((x: GameEntry): boolean => x.titleId.toUpperCase() === key);
  const out: GameItem[] = items.slice();
  if (g === undefined) {
    // deleted: keep the row only if the server still offers it, as Available
    if (idx >= 0 && out[idx].source === ItemSource.Both) {
      const old: GameItem = out[idx];
      out[idx] = {
        titleId: old.titleId, name: old.name, source: ItemSource.Remote, state: ItemState.Available,
        root: '', installedStamp: '', remoteStamp: old.remoteStamp, bytes: old.bytes, sizeLabel: old.sizeLabel,
        coverPath: '', perf: null, perfText: '暂无性能数据', version: '', note: '', progress: -1, url: old.url
      };
    } else if (idx >= 0) {
      out.splice(idx, 1);
    }
    return out;
  }
  const remote: RemotePack | null = idx >= 0 && out[idx].remoteStamp.length > 0
    ? { title: out[idx].titleId, name: out[idx].name, bytes: out[idx].bytes,
      stamp: out[idx].remoteStamp, url: out[idx].url } : null;
  const fresh: GameItem = fromLocal(g, remote);
  if (idx >= 0) out[idx] = fresh;
  else out.push(fresh);
  return out;
}

/**
 * The settings' stored source, read once per load.
 *
 * The library store is the one SettingsPage writes (SettingsPage.ets:110 `putSync(PREF_SOURCE, norm)`), so the
 * library never invents a second place for it.
 */
export const CATALOG_PREF: string = PREF_SOURCE;
