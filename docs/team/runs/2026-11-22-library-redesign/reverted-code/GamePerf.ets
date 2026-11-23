/*
 * GamePerf.ets - one game's measured performance and its pictures, as one record type.
 *
 * The library and the download list show the same facts about the same title, so they read the same files:
 *
 *   <root>/perf.json    the measurements, newest last (schema 1)
 *   <root>/media.json   the picture index: which file is the cover, which are scene shots
 *   <root>/cover.jpg    the cover the card draws (falls back to the pack's own icon.jpg)
 *   <root>/shots/*.jpg  scene shots, one per sample, named <scene>_<epoch>.jpg
 *
 * Where they come from, and why here:
 *   - perf.json lives at the game root, NOT under mods/ RomFS/ aoc/, because PackImport.importPack only removes
 *     those three folders on a re-import (PackImport.ets:135). A measurement therefore survives a pack update,
 *     which is the whole point: the number describes the device, not the pack.
 *   - the pack itself may carry a perf.json built on the porter's machine (perf_report.py); the import brings it
 *     in with the rest of the tree, and a device record is appended beside it, tagged by device model.
 *
 * Every record carries its own device: a number without "on which phone" is not a claim, it is a rumour.
 */
import { fileIo as fs } from '@kit.CoreFileKit';
import { deviceInfo } from '@kit.BasicServicesKit';
import { display } from '@kit.ArkUI';

/** The device a record was measured on. Kept flat and small: it is repeated in every record. */
export interface PerfDevice {
  model: string;        // deviceInfo.productModel, e.g. HBN-AL80 (the key a reader compares on)
  market: string;       // deviceInfo.marketName, e.g. HUAWEI Pura 70 Pro+
  soc: string;          // deviceInfo.hardwareModel
  api: number;          // deviceInfo.sdkApiVersion
  width: number;        // the surface width in px at capture time
  height: number;
}

/** One measurement window of one scene. All costs are milliseconds per frame, p50 over the window. */
export interface PerfRecord {
  schema: number;       // 1
  titleId: string;      // the 16-hex title ID: the join key with the library and the pack header
  source: string;       // 'device' (measured here) | 'porter' (measured on the build machine)
  scene: string;        // a short label: 'field', 'shrine', 'menu', 'cutscene'
  device: PerfDevice;
  fpsP50: number;       // 1000 / frameMsP50, rounded: the number a player quotes
  frameMsP50: number;   // dmhost's timing report, p50
  gpuMsP50: number;     // GPU timestamp span, p50
  replayMsP50: number;  // the replay thread's own time, p50
  setsMsP50: number;    // stage_set total, p50
  frames: number;       // frames in the window: a 20-frame window is noise, a 600-frame one is a claim
  capturedAt: string;   // ISO 8601 with the local offset
  appVersion: string;   // the HAP's versionName, so an old number is not read as a current one
  stamp: string;        // the pack stamp the measurement belongs to
}

/** perf.json: the records of one title. */
export interface PerfFile {
  schema: number;
  titleId: string;
  records: PerfRecord[];
}

/** One picture. `kind` is 'cover' (the card's art) or 'scene' (a shot behind a sample). */
export interface MediaShot {
  file: string;         // relative to the game root
  kind: string;
  scene: string;
  titleId: string;
  at: string;           // ISO 8601
  width: number;
  height: number;
}

/** media.json: what pictures this title has. */
export interface MediaFile {
  schema: number;
  titleId: string;
  cover: string;        // relative path, '' when there is none
  shots: MediaShot[];
}

export const PERF_SCHEMA: number = 1;
const PERF_FILE: string = '/perf.json';
const MEDIA_FILE: string = '/media.json';
const COVER_FILE: string = '/cover.jpg';
const LEGACY_ICON: string = '/icon.jpg';
/** Records kept per title: the last 40 samples, oldest dropped. Enough for a history, small enough to read. */
const PERF_KEEP: number = 40;
/** A window shorter than this is not a measurement. */
export const PERF_MIN_FRAMES: number = 120;

/** This device, as every record describes it. */
export function thisDevice(): PerfDevice {
  let w: number = 0;
  let h: number = 0;
  try {
    const d = display.getDefaultDisplaySync();
    w = d.width;
    h = d.height;
  } catch (e) {
    w = 0;
    h = 0;
  }
  return {
    model: typeof deviceInfo.productModel === 'string' ? deviceInfo.productModel : '',
    market: typeof deviceInfo.marketName === 'string' ? deviceInfo.marketName : '',
    soc: typeof deviceInfo.hardwareModel === 'string' ? deviceInfo.hardwareModel : '',
    api: typeof deviceInfo.sdkApiVersion === 'number' ? deviceInfo.sdkApiVersion : 0,
    width: w,
    height: h
  };
}

/** The fps a frame time implies, rounded: 16.7 ms -> 60. */
export function fpsOf(frameMs: number): number {
  if (frameMs <= 0) return 0;
  return Math.round(1000 / frameMs);
}

/** '60 fps · 16.7 ms · HBN-AL80': one line for a card or a row. */
export function perfLine(r: PerfRecord): string {
  if (r.frameMsP50 <= 0) return '暂无性能数据';
  return `${r.fpsP50} fps · ${r.frameMsP50.toFixed(1)} ms · ${r.device.model}`;
}

/** The performance file's path for a game root. */
export function perfPath(root: string): string {
  return root + PERF_FILE;
}

/** The picture index's path for a game root. */
export function mediaPath(root: string): string {
  return root + MEDIA_FILE;
}

/**
 * Read a title's records. A missing or unreadable file is null, never a throw: the library must list a game whose
 * measurement is absent, and say so, rather than fail the whole grid (the trap LibraryPage.ets:127 already names).
 */
export function readPerf(root: string): PerfFile | null {
  try {
    const text: string = fs.readTextSync(root + PERF_FILE);
    const j = JSON.parse(text) as Record<string, Object>;
    const list = j['records'];
    if (!Array.isArray(list)) return null;
    const out: PerfRecord[] = [];
    for (const raw of list as Object[]) {
      const r = raw as Record<string, Object>;
      const dev = r['device'] as Record<string, Object> | undefined;
      const titleId = r['titleId'];
      const frame = r['frameMsP50'];
      if (typeof titleId !== 'string' || typeof frame !== 'number') continue;
      out.push({
        schema: typeof r['schema'] === 'number' ? r['schema'] as number : PERF_SCHEMA,
        titleId: titleId,
        source: typeof r['source'] === 'string' ? r['source'] as string : 'device',
        scene: typeof r['scene'] === 'string' ? r['scene'] as string : '',
        device: {
          model: dev !== undefined && typeof dev['model'] === 'string' ? dev['model'] as string : '',
          market: dev !== undefined && typeof dev['market'] === 'string' ? dev['market'] as string : '',
          soc: dev !== undefined && typeof dev['soc'] === 'string' ? dev['soc'] as string : '',
          api: dev !== undefined && typeof dev['api'] === 'number' ? dev['api'] as number : 0,
          width: dev !== undefined && typeof dev['width'] === 'number' ? dev['width'] as number : 0,
          height: dev !== undefined && typeof dev['height'] === 'number' ? dev['height'] as number : 0
        },
        fpsP50: typeof r['fpsP50'] === 'number' ? r['fpsP50'] as number : fpsOf(frame as number),
        frameMsP50: frame as number,
        gpuMsP50: typeof r['gpuMsP50'] === 'number' ? r['gpuMsP50'] as number : 0,
        replayMsP50: typeof r['replayMsP50'] === 'number' ? r['replayMsP50'] as number : 0,
        setsMsP50: typeof r['setsMsP50'] === 'number' ? r['setsMsP50'] as number : 0,
        frames: typeof r['frames'] === 'number' ? r['frames'] as number : 0,
        capturedAt: typeof r['capturedAt'] === 'string' ? r['capturedAt'] as string : '',
        appVersion: typeof r['appVersion'] === 'string' ? r['appVersion'] as string : '',
        stamp: typeof r['stamp'] === 'string' ? r['stamp'] as string : ''
      });
    }
    const id: Object | undefined = j['titleId'];
    return { schema: PERF_SCHEMA, titleId: typeof id === 'string' ? id : '', records: out };
  } catch (e) {
    return null;
  }
}

/**
 * Write a title's records.
 *
 * Through a temp file and a rename: a write torn by the app being killed would otherwise leave perf.json half
 * parsed, and the reader above would silently report "no data" for a game that has plenty.
 */
export function writePerf(root: string, file: PerfFile): boolean {
  const tmp: string = root + PERF_FILE + '.tmp';
  try {
    const f = fs.openSync(tmp, fs.OpenMode.CREATE | fs.OpenMode.READ_WRITE | fs.OpenMode.TRUNC);
    try {
      fs.writeSync(f.fd, JSON.stringify(file));
    } finally {
      fs.closeSync(f);
    }
    fs.renameSync(tmp, root + PERF_FILE);
    return true;
  } catch (e) {
    try { fs.unlinkSync(tmp); } catch (e2) { }
    return false;
  }
}

/** ISO 8601 with this device's offset, so a record reads as a date and not as an epoch. */
export function isoNow(): string {
  const d: Date = new Date();
  const off: number = -d.getTimezoneOffset();
  const sign: string = off >= 0 ? '+' : '-';
  const hh: string = String(Math.floor(Math.abs(off) / 60)).padStart(2, '0');
  const mm: string = String(Math.abs(off) % 60).padStart(2, '0');
  const p = (n: number): string => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}${sign}${hh}:${mm}`;
}

/**
 * Append one measurement to a title's records and keep the file.
 *
 * The caller decides when: a sample is written when a window closes with enough frames in it, and once more when
 * the game page goes away, so a run interrupted by the back key still leaves its number.
 */
export function appendPerf(root: string, rec: PerfRecord): PerfFile {
  const cur: PerfFile | null = readPerf(root);
  const file: PerfFile = cur !== null ? cur : { schema: PERF_SCHEMA, titleId: rec.titleId, records: [] };
  file.titleId = rec.titleId;
  // A device record replaces the one from the same scene and model within the same minute: a repeated poll must
  // not turn a history into a log of the same second.
  const key: string = `${rec.device.model}|${rec.scene}|${rec.stamp}|${rec.capturedAt.substring(0, 16)}`;
  const kept: PerfRecord[] = [];
  for (const r of file.records) {
    const k: string = `${r.device.model}|${r.scene}|${r.stamp}|${r.capturedAt.substring(0, 16)}`;
    if (k !== key) kept.push(r);
  }
  kept.push(rec);
  file.records = kept.length > PERF_KEEP ? kept.slice(kept.length - PERF_KEEP) : kept;
  writePerf(root, file);
  return file;
}

/**
 * The record a card should show: this device's newest for the title, else the newest porter record.
 *
 * Same-model-first matters: a number measured on the porter's machine is a baseline, and putting it in the same
 * font as the player's own measurement would be the same mistake as one badge for two different facts.
 */
export function bestPerf(file: PerfFile | null, model: string): PerfRecord | null {
  if (file === null || file.records.length === 0) return null;
  let own: PerfRecord | null = null;
  let any: PerfRecord | null = null;
  for (const r of file.records) {
    if (any === null || r.capturedAt > any.capturedAt) any = r;
    if (r.device.model === model && (own === null || r.capturedAt > own.capturedAt)) own = r;
  }
  return own !== null ? own : any;
}

/** Read a title's picture index, or null when it has none. */
export function readMedia(root: string): MediaFile | null {
  try {
    const text: string = fs.readTextSync(root + MEDIA_FILE);
    const j = JSON.parse(text) as Record<string, Object>;
    const shots: MediaShot[] = [];
    const raw = j['shots'];
    if (Array.isArray(raw)) {
      for (const o of raw as Object[]) {
        const s = o as Record<string, Object>;
        const file = s['file'];
        if (typeof file !== 'string') continue;
        shots.push({
          file: file,
          kind: typeof s['kind'] === 'string' ? s['kind'] as string : 'scene',
          scene: typeof s['scene'] === 'string' ? s['scene'] as string : '',
          titleId: typeof s['titleId'] === 'string' ? s['titleId'] as string : '',
          at: typeof s['at'] === 'string' ? s['at'] as string : '',
          width: typeof s['width'] === 'number' ? s['width'] as number : 0,
          height: typeof s['height'] === 'number' ? s['height'] as number : 0
        });
      }
    }
    const cover = j['cover'];
    return {
      schema: PERF_SCHEMA,
      titleId: typeof j['titleId'] === 'string' ? j['titleId'] as string : '',
      cover: typeof cover === 'string' ? cover : '',
      shots: shots
    };
  } catch (e) {
    return null;
  }
}

/** Record one picture in the index (the file itself is written by the capture path, not here). */
export function addShot(root: string, shot: MediaShot): boolean {
  const cur: MediaFile | null = readMedia(root);
  const file: MediaFile = cur !== null ? cur : { schema: PERF_SCHEMA, titleId: shot.titleId, cover: '', shots: [] };
  if (shot.kind === 'cover') file.cover = shot.file;
  file.shots.push(shot);
  if (file.shots.length > 200) file.shots = file.shots.slice(file.shots.length - 200);
  try {
    const f = fs.openSync(root + MEDIA_FILE, fs.OpenMode.CREATE | fs.OpenMode.READ_WRITE | fs.OpenMode.TRUNC);
    try {
      fs.writeSync(f.fd, JSON.stringify(file));
    } finally {
      fs.closeSync(f);
    }
    return true;
  } catch (e) {
    return false;
  }
}

/**
 * The cover to draw: media.json's cover, else cover.jpg, else the pack's own icon.jpg, else ''.
 *
 * The order is the point: a captured frame beats a shipped icon (it is this device's picture of this build), and a
 * pack with neither still gets the card's generated placeholder (GameCard.ets:64).
 */
export function coverPath(root: string): string {
  const media: MediaFile | null = readMedia(root);
  if (media !== null && media.cover.length > 0) {
    try {
      if (fs.accessSync(root + '/' + media.cover)) return root + '/' + media.cover;
    } catch (e) {
      // the index names a file that is gone: fall through
    }
  }
  for (const name of [COVER_FILE, LEGACY_ICON]) {
    try {
      if (fs.accessSync(root + name)) return root + name;
    } catch (e) {
      // try the next
    }
  }
  return '';
}

/** The pack's own metadata as text, for the record's stamp/version: no throw when the pack has none. */
export function packStampOf(root: string): string {
  try {
    return fs.readTextSync(root + '/.stamp').trim();
  } catch (e) {
    return '';
  }
}

/** Encode an ISO date for a file name: 2026-11-22T20:34:00+08:00 -> 20261122T203400. */
export function fileStamp(iso: string): string {
  return iso.replace(/[-:]/g, '').replace(/(\+|-)\d\d:\d\d$/, '').substring(0, 15);
}
