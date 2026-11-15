"""Serve a game data pack over loopback http for the OHOS app's own import (P2.1 dev path).

Neither the shell domain nor the app can cross the SELinux boundary (the shell cannot write the app sandbox; the
app cannot read /data/local/tmp), so the pack travels over http: this server serves it, `hdc rport tcp:PORT tcp:PORT`
forwards the device's 127.0.0.1:PORT here, and the app pulls it into its own sandbox.

Two shapes are served:

  /ping              the app's probe ("PONG")
  /pack.nxpk         the whole container as ONE stream (the normal path: one request, one sequential read)
  /dm.files.tsv      the manifest alone (the legacy per-file path's probe)
  /<path>            one pack file (the legacy per-file path, kept for comparison)

    python tools/pack_server.py --pack <porter pack dir> --manifest <dm.files.tsv> [--nxpk <file.nxpk>] [--port 28899]
"""
import argparse, http.server, os, socketserver, sys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pack', required=True, help='the porter data pack folder (nx2a-data.json + RomFS/ + mods/)')
    ap.add_argument('--manifest', help='the dm.files.tsv that describes the pack (the legacy per-file path)')
    ap.add_argument('--nxpk', help='the .nxpk container to serve at /pack.nxpk (the single-stream path)')
    ap.add_argument('--port', type=int, default=28899)
    ap.add_argument('--bind', default='127.0.0.1')
    a = ap.parse_args()
    pack = os.path.abspath(a.pack)
    man = os.path.abspath(a.manifest) if a.manifest else ''
    nxpk = os.path.abspath(a.nxpk) if a.nxpk else ''
    if not os.path.isdir(pack): sys.exit('pack_server: no such pack: %s' % pack)
    if man and not os.path.isfile(man): sys.exit('pack_server: no such manifest: %s' % man)
    if nxpk and not os.path.isfile(nxpk): sys.exit('pack_server: no such container: %s' % nxpk)

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

        def do_HEAD(self):
            if self.path == '/pack.nxpk' and nxpk:
                self._send_file(nxpk, head_only=True)
                return
            self.send_response(404)
            self.end_headers()

        def do_GET(self):
            if self.path == '/ping':
                body = b'PONG'
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
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
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            print('pack_server: stopped')


if __name__ == '__main__':
    main()
