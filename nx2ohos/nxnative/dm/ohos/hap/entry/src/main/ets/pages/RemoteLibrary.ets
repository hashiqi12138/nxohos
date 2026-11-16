/**
 * P3.3: the library's remote source.
 *
 * A pack server (dm/tools/pack_server.py, started with --games-dir and --bind 0.0.0.0) publishes what it holds at
 * /list; this module reads that list and streams one container down for PackImport.importPack. The server and the
 * phone are on the same network, so no `hdc rport` is needed: the user types the address once, it is kept in
 * preferences, and the library shows the server's packs beside the installed ones.
 *
 * The download is the same shape the loopback path uses (a stream to a temp file, then the shared tar reader): a
 * multi-GB body must never be buffered in memory - an ARRAY_BUFFER result dies around 52 MB with code 2300023.
 */
import fs from '@ohos.file.fs';
import http from '@ohos.net.http';
import util from '@ohos.util';

/** One container the server offers. */
export interface RemotePack {
  title: string;      // the 16-hex title ID the container's header carries
  name: string;       // the file name, which the library shows until the pack is imported
  bytes: number;      // its size, for the row and the progress bar
  stamp: string;      // the container's stamp: equal to the sandbox's, the import is skipped
  url: string;        // '/packs/<file>' or '/pack.nxpk', relative to the server
}

/** The stored source: '' means none, so the library shows only what is installed. */
export const PREF_SOURCE: string = 'library_source';

/** 'http://host:port' as typed, made usable: a bare host gets http:// and the pack server's default port. */
export function normalizeSource(text: string): string {
  let s: string = text.trim();
  if (!s.length) return '';
  if (!/^https?:\/\//i.test(s)) s = 'http://' + s;
  s = s.replace(/\/+$/, '');
  // a bare host (or one with only a colon-free port-less form) gets the default port
  const m: RegExpMatchArray | null = s.match(/^https?:\/\/([^/:]+)(?::(\d+))?$/i);
  if (m !== null && m[2] === undefined) s = s + ':28899';
  return s;
}

/** The packs the server holds, by name. Throws with the server's reason when it cannot be read. */
export async function listRemote(source: string): Promise<RemotePack[]> {
  const req = http.createHttp();
  try {
    const r = await req.request(source + '/list', {
      method: http.RequestMethod.GET,
      expectDataType: http.HttpDataType.STRING,
      connectTimeout: 4000,
      readTimeout: 8000
    });
    if (r.responseCode !== 200 || typeof r.result !== 'string') {
      throw new Error('HTTP ' + r.responseCode);
    }
    const raw: object[] = JSON.parse(r.result as string) as object[];
    const out: RemotePack[] = [];
    for (const o of raw) {
      const j = o as Record<string, Object>;
      const title = j['title'];
      const file = j['file'];
      const url = j['url'];
      if (typeof title !== 'string' || typeof url !== 'string') continue;
      // an explicit literal of the declared interface: ArkTS refuses an untyped object literal
      const pack: RemotePack = {
        title: title,
        name: typeof file === 'string' ? file : title,
        bytes: typeof j['bytes'] === 'number' ? j['bytes'] as number : 0,
        stamp: typeof j['stamp'] === 'string' ? j['stamp'] as string : '',
        url: url
      };
      out.push(pack);
    }
    return out;
  } finally {
    req.destroy();
  }
}

/**
 * Stream one container into `destPath`, resuming a partial file when one is there.
 *
 * Returns the byte count. A server that answers 206 for a Range request continues where the file stopped; one that
 * answers 200 starts over, which is what the file position is reset to.
 */
export async function downloadPack(source: string, pack: RemotePack, destPath: string,
                                   onProgress: (got: number, total: number) => void): Promise<number> {
  let have: number = 0;
  try {
    have = fs.statSync(destPath).size;
  } catch (e) {
    have = 0;
  }
  if (have > 0 && pack.bytes > 0 && have >= pack.bytes) return have;   // already complete
  const req = http.createHttp();
  // One descriptor for the whole download, not one per chunk: the first version opened, wrote and closed the file
  // for every chunk, which is tens of thousands of open/close pairs for a 1.17 GB body and died partway through with
  // 'file_api: Failed to open file by ret: -2' (measured 2026-11-16: the file stopped at 438 MB of 1.17 GB, and the
  // row sat at a percentage that never moved).
  let fd: number = -1;
  try {
    fd = fs.openSync(destPath, fs.OpenMode.READ_WRITE | fs.OpenMode.CREATE).fd;
    fs.truncateSync(fd, have);
    // requestInStream's own callback is AsyncCallback<number, void> (it reports the final status, not the bytes);
    // the chunks arrive on the 'dataReceive' event, which is the only place a stream's body can be written to disk -
    // an ARRAY_BUFFER result would buffer the whole multi-GB body in memory (it dies around 52 MB, code 2300023).
    let pos: number = have;
    req.on('dataReceive', (chunk: ArrayBuffer) => {
      if (fd < 0) return;
      try {
        fs.writeSync(fd, chunk, { offset: pos });
        pos += chunk.byteLength;
        onProgress(pos, pack.bytes);
      } catch (e) {
        // a write that fails mid-stream ends the download; the bytes already on disk stay, so the next attempt
        // resumes from them
        fs.closeSync(fd);
        fd = -1;
      }
    });
    // A resumed download asks for the rest of the body; the server answers 206 with the remaining bytes.
    const hdr: Record<string, string> = have > 0 ? { 'Range': 'bytes=' + have + '-' } : {};
    const code: number = await req.requestInStream(source + pack.url, {
      method: http.RequestMethod.GET,
      header: hdr,
      connectTimeout: 10000,
      readTimeout: 600000
    });
    if (code !== 200 && code !== 206) {
      throw new Error('HTTP ' + code);
    }
    // requestInStream resolves when the response is done, but the 'dataReceive' callbacks are dispatched on another
    // thread: a body whose last chunks are still queued would look complete here. The file on disk is the authority -
    // a short file is a failed download, and the caller's next attempt resumes from what did arrive.
    let onDisk: number = 0;
    try {
      onDisk = fs.statSync(destPath).size;
    } catch (e) {
      throw new Error('下载失败：文件未写入');
    }
    if (pack.bytes > 0 && onDisk < pack.bytes) {
      throw new Error('下载不完整（' + Math.floor(onDisk * 100 / pack.bytes) + '%）');
    }
    return onDisk;
  } finally {
    if (fd >= 0) {
      try { fs.closeSync(fd); } catch (e) { }
    }
    req.destroy();
  }
}
