"""Serve game data packs over http for the OHOS app's own import (P2.1 dev path, and the library's download source).

Neither the shell domain nor the app can cross the SELinux boundary (the shell cannot write the app sandbox; the
app cannot read /data/local/tmp), so the pack travels over http: this server serves it and the app pulls it into its
own sandbox. Two ways to reach it:

  * over `hdc rport tcp:PORT tcp:PORT`, the device's 127.0.0.1:PORT reaches a server bound to 127.0.0.1 here;
  * over the LAN, bound to 0.0.0.0, the app's "library source" points at http://<this machine>:PORT and lists what
    the server holds.

Endpoints:

  /ping              the app's probe ("PONG")
  /list              the packs this server holds, as JSON: [{title, name, version, bytes, stamp, url}]
  /pack.nxpk         the container given with --nxpk, as ONE stream (the normal path: one request, sequential read)
  /packs/<file>.nxpk one container out of --games-dir (the library's download)
  /stamp             the 80-byte header of --nxpk alone (the app checks it before pulling gigabytes)
  /dm.files.tsv      the manifest alone (the legacy per-file path's probe)
  /<path>            one pack file (the legacy per-file path, kept for comparison)

    python tools/pack_server.py --pack <porter pack dir> [--manifest <dm.files.tsv>] [--nxpk <file.nxpk>]
                               [--games-dir <folder of .nxpk>] [--port 28899] [--bind 0.0.0.0]
"""
import argparse, http.server, json, os, socketserver, sys

HEADER = 80   # the .nxpk container header: "NXPK" + version + title ID + stamp + tar size


def pack_info(path):
    """One container's header as the library lists it: the title it carries, and its stamp (what the app compares)."""
    try:
        with open(path, 'rb') as f:
            h = f.read(HEADER)
    except OSError:
        return None
    if len(h) < HEADER or h[:4] != b'NXPK':
        return None
    title = h[8:8 + 16].split(b'\0')[0].decode('ascii', 'replace').strip()
    stamp = h[24:24 + 48].split(b'\0')[0].decode('ascii', 'replace').strip()
    # the name is the pack's own nx2a-data.json, which costs a second read of the tar: the app shows the title ID
    # until it has imported it, and the library's label is the file name here
    return {'title': title, 'stamp': stamp, 'bytes': os.path.getsize(path), 'file': os.path.basename(path)}


def scan_games(folder):
    """Every .nxpk in folder, by name (the library's order)."""
    out = []
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return out
    for n in names:
        if not n.lower().endswith('.nxpk'):
            continue
        info = pack_info(os.path.join(folder, n))
        if info:
            out.append(info)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pack', required=True, help='the porter data pack folder (nx2a-data.json + RomFS/ + mods/)')
    ap.add_argument('--manifest', help='the dm.files.tsv that describes the pack (the legacy per-file path)')
    ap.add_argument('--nxpk', help='the .nxpk container to serve at /pack.nxpk (the single-stream path)')
    ap.add_argument('--games-dir', help='a folder of .nxpk containers the library lists at /list and serves at /packs/')
    ap.add_argument('--port', type=int, default=28899)
    ap.add_argument('--bind', default='127.0.0.1', help="0.0.0.0 makes it reachable over the LAN (the library's source)")
    a = ap.parse_args()
    pack = os.path.abspath(a.pack)
    man = os.path.abspath(a.manifest) if a.manifest else ''
    nxpk = os.path.abspath(a.nxpk) if a.nxpk else ''
    games = os.path.abspath(a.games_dir) if a.games_dir else ''
    if not os.path.isdir(pack): sys.exit('pack_server: no such pack: %s' % pack)
    if man and not os.path.isfile(man): sys.exit('pack_server: no such manifest: %s' % man)
    if nxpk and not os.path.isfile(nxpk): sys.exit('pack_server: no such container: %s' % nxpk)
    if games and not os.path.isdir(games): sys.exit('pack_server: no such games folder: %s' % games)

    class Handler(http.server.SimpleHTTPRequestHandler):
        # HTTP/1.1 keeps the connection open and honours Content-Length; the 1.0 default closes the socket after
        # each response, which the app's http client reports as "Transferred a partial file" (code 2300018) even
        # when every byte arrived.
        protocol_version = 'HTTP/1.1'
        # a 14.75 GB body over WiFi runs for an hour: do not let the handler time out mid-send
        timeout = 3600

        def __init__(self, *args, **kw):
            super().__init__(*args, directory=pack, **kw)

        def _send_file(self, path, head_only=False):
            size = os.path.getsize(path)
            start = 0
            # Range: a resume after an interrupted download asks for the bytes it is missing (the app keeps the
            # partial .nxpk and sends "Range: bytes=<have>-"). 206 tells it the request was honoured.
            rng = self.headers.get('Range', '')
            if rng.startswith('bytes='):
                spec = rng[len('bytes='):].split(',')[0].strip()
                first = spec.split('-')[0]
                if first.isdigit():
                    start = int(first)
                    if start > size:
                        start = size
            body_len = size - start
            if start > 0:
                self.send_response(206)
                self.send_header('Content-Range', 'bytes %d-%d/%d' % (start, size - 1, size))
            else:
                self.send_response(200)
            self.send_header('Content-Type', 'application/octet-stream')
            self.send_header('Content-Length', str(body_len))
            self.send_header('Accept-Ranges', 'bytes')
            self.end_headers()
            if head_only:
                return
            with open(path, 'rb') as f:
                if start:
                    f.seek(start)
                sent = 0
                while True:
                    buf = f.read(1 << 20)
                    if not buf:
                        break
                    try:
                        self.wfile.write(buf)
                        sent += len(buf)
                    except (BrokenPipeError, ConnectionResetError):
                        # the app went away (a killed round, a resume): stop quietly, the .part keeps what
                        # arrived and the next request continues from there
                        return
                try:
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    return
                if sent != body_len:
                    sys.stderr.write('pack_server: sent %d of %d bytes for %s\n' % (sent, body_len, self.path))
                    sys.stderr.flush()

        def _not_found(self):
            """404 with a length, and with the connection closed.

            protocol_version is HTTP/1.1, so a response without Content-Length leaves the client waiting for a body
            that never comes: the app's http client sat on a missing pack until its own timeout fired (measured
            2026-11-16, a test that hung instead of reading a 404). An empty body with an explicit zero length, and
            the connection closed, ends the exchange at once.
            """
            self.send_response(404)
            self.send_header('Content-Length', '0')
            self.send_header('Connection', 'close')
            self.end_headers()
            self.close_connection = True

        def do_HEAD(self):
            if self.path == '/pack.nxpk' and nxpk:
                self._send_file(nxpk, head_only=True)
                return
            self._not_found()

        def do_GET(self):
            if self.path == '/ping':
                body = b'PONG'
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path == '/list':
                # What the library shows: every container the server holds, by name. The app asks this when its
                # source is set to this server, then downloads one with /packs/<file>.
                items = scan_games(games) if games else ([dict(pack_info(nxpk), url='/pack.nxpk')] if nxpk else [])
                body = json.dumps([dict(i, url=i.get('url') or '/packs/' + i['file']) for i in items]).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path.startswith('/packs/') and games:
                name = os.path.basename(self.path[len('/packs/'):])
                target = os.path.join(games, name)
                if name.lower().endswith('.nxpk') and os.path.isfile(target):
                    self._send_file(target)
                    return
                self._not_found()
                return
            if self.path == '/pack.nxpk' and nxpk:
                self._send_file(nxpk)
                return
            if self.path == '/stamp' and nxpk:
                # the container's header alone: the app checks this before pulling 14.75 GB, so a round whose
                # data is already in the sandbox costs one 80-byte request instead of a full download
                with open(nxpk, 'rb') as f:
                    body = f.read(80)
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path == '/dm.files.tsv' and man:
                self._send_file(man)
                return
            super().do_GET()

        def log_message(self, fmt, *args):
            pass   # 41k requests would drown the console; progress is the app's own log

    socketserver.ThreadingTCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer((a.bind, a.port), Handler) as srv:
        srv.daemon_threads = True
        print('pack_server: serving %s on http://%s:%d' % (pack, a.bind, a.port), flush=True)
        print('  /pack.nxpk -> %s' % (nxpk or '(none)'), flush=True)
        print('  /dm.files.tsv -> %s' % (man or '(none)'), flush=True)
        if games:
            held = scan_games(games)
            print('  /list -> %d container(s) in %s' % (len(held), games), flush=True)
            for i in held:
                print('    %-40s %016s %8.2f GB' % (i['file'], i['title'], i['bytes'] / 2**30), flush=True)
            if a.bind == '127.0.0.1':
                print('  (bound to 127.0.0.1: reachable through `hdc rport`; use --bind 0.0.0.0 for the LAN)', flush=True)
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            print('pack_server: stopped')


if __name__ == '__main__':
    main()
