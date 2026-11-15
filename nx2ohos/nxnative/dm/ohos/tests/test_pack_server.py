"""pack_server.py: the HTTP surface the app pulls a pack from.

A real server is started on an ephemeral port and driven with urllib, so the tests exercise the handler as the app
does - including the Range resume, the 80-byte /stamp probe, and the HTTP/1.1 keep-alive that the app's client
depends on (HTTP/1.0 made it report "Transferred a partial file" after every byte had arrived).
"""
import http.client
import json
import socket
import struct
import threading
import time
import urllib.error
import urllib.request

import pytest


def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def server(pack_server, tmp_path, monkeypatch):
    """Start pack_server.main() in a thread on a free port; yields (port, pack_dir, nxpk_path, manifest_path)."""
    pack = tmp_path / 'pack'
    (pack / 'RomFS').mkdir(parents=True)
    (pack / 'nx2a-data.json').write_text(json.dumps({'title_id': 'T'}), encoding='utf-8')
    (pack / 'RomFS' / 'a.bin').write_bytes(b'A' * 100)
    (pack / 'env.txt').write_text('X=1\n', encoding='utf-8')
    manifest = tmp_path / 'dm.files.tsv'
    manifest.write_text('# DM_DATA abc123\nRomFS/a.bin\t100\t1\n', encoding='utf-8')

    # a small container: 80-byte header + 300 bytes of body
    header = b'NXPK' + bytes([1]) + b'\x00\x00\x00' + b'T'.ljust(16, b'\x00') + b's'.ljust(48, b'\x00') \
        + struct.pack('<Q', 300)
    nxpk = tmp_path / 'game.nxpk'
    nxpk.write_bytes(header + bytes(range(256)) * 2)[:380] if False else nxpk.write_bytes(header + b'B' * 300)

    port = free_port()
    argv = ['pack_server.py', '--pack', str(pack), '--manifest', str(manifest),
            '--nxpk', str(nxpk), '--port', str(port)]
    monkeypatch.setattr('sys.argv', argv)

    # serve_forever blocks: run main() in a daemon thread and wait for the port to answer
    t = threading.Thread(target=pack_server.main, daemon=True)
    t.start()
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=0.2):
                break
        except OSError:
            time.sleep(0.02)
    else:
        pytest.fail('pack_server did not start')

    yield port, pack, nxpk, manifest


def get(port, path, headers=None, method='GET'):
    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
    conn.request(method, path, headers=headers or {})
    r = conn.getresponse()
    body = r.read()
    out = (r.status, dict(r.getheaders()), body)
    conn.close()
    return out


# ---- /ping ----

def test_ping_answers_pong(server):
    port = server[0]
    status, headers, body = get(port, '/ping')
    assert status == 200
    assert body == b'PONG'
    assert headers['Content-Length'] == '4'


# ---- /stamp: the 80-byte probe the app makes before pulling 14 GB ----

def test_stamp_returns_exactly_the_header(server):
    port, _pack, nxpk, _man = server
    status, headers, body = get(port, '/stamp')
    assert status == 200
    assert len(body) == 80
    assert body == nxpk.read_bytes()[:80]
    assert body[:4] == b'NXPK'


def test_stamp_body_is_the_stamp_field(server):
    port, _pack, nxpk, _man = server
    _s, _h, body = get(port, '/stamp')
    # the app compares this against its sandbox's .stamp
    assert body[24:72].rstrip(b'\x00') == b's'


# ---- /pack.nxpk: the whole container ----

def test_pack_serves_the_whole_container(server):
    port, _pack, nxpk, _man = server
    status, headers, body = get(port, '/pack.nxpk')
    assert status == 200
    assert body == nxpk.read_bytes()
    assert headers['Content-Length'] == str(nxpk.stat().st_size)
    assert headers['Accept-Ranges'] == 'bytes'


def test_pack_advertises_octet_stream(server):
    port = server[0]
    _s, headers, _b = get(port, '/pack.nxpk')
    assert headers['Content-Type'] == 'application/octet-stream'


# ---- Range: the resume path ----

def test_range_returns_206_with_content_range(server):
    port, _pack, nxpk, _man = server
    status, headers, body = get(port, '/pack.nxpk', {'Range': 'bytes=100-'})
    assert status == 206
    assert headers['Content-Range'] == 'bytes 100-%d/%d' % (nxpk.stat().st_size - 1, nxpk.stat().st_size)
    assert body == nxpk.read_bytes()[100:]


def test_range_start_zero_is_a_plain_200(server):
    """bytes=0- is the same as no Range: the app sends it after a kill at byte 0."""
    port = server[0]
    status, _h, _b = get(port, '/pack.nxpk', {'Range': 'bytes=0-'})
    assert status == 200


def test_range_past_the_end_yields_an_empty_body(server):
    port, _pack, nxpk, _man = server
    status, headers, body = get(port, '/pack.nxpk', {'Range': 'bytes=%d-' % (nxpk.stat().st_size + 50)})
    # start is clamped to the size, so the response is a 206 with nothing left to send
    assert status == 206
    assert body == b''
    assert headers['Content-Length'] == '0'


def test_range_with_a_multi_range_takes_the_first(server):
    port, _pack, nxpk, _man = server
    status, _h, body = get(port, '/pack.nxpk', {'Range': 'bytes=10-19,50-59'})
    assert status == 206
    assert body == nxpk.read_bytes()[10:]


def test_range_that_is_not_a_number_is_ignored(server):
    port = server[0]
    status, _h, _b = get(port, '/pack.nxpk', {'Range': 'bytes=abc-'})
    assert status == 200


def test_range_without_the_bytes_prefix_is_ignored(server):
    port = server[0]
    status, _h, _b = get(port, '/pack.nxpk', {'Range': 'items=10-'})
    assert status == 200


# ---- HEAD ----

def test_head_reports_the_size_without_a_body(server):
    port, _pack, nxpk, _man = server
    status, headers, body = get(port, '/pack.nxpk', method='HEAD')
    assert status == 200
    assert body == b''
    assert headers['Content-Length'] == str(nxpk.stat().st_size)


def test_head_on_another_path_is_404(server):
    port = server[0]
    status, _h, body = get(port, '/nothing', method='HEAD')
    assert status == 404
    assert body == b''


# ---- the manifest and the legacy per-file path ----

def test_manifest_is_served_whole(server):
    port, _pack, _nxpk, man = server
    status, _h, body = get(port, '/dm.files.tsv')
    assert status == 200
    assert body == man.read_bytes()


def test_a_pack_file_is_served_by_path(server):
    """The legacy per-file path: SimpleHTTPRequestHandler serves the pack dir."""
    port = server[0]
    status, _h, body = get(port, '/RomFS/a.bin')
    assert status == 200
    assert body == b'A' * 100


def test_a_missing_file_is_404(server):
    port = server[0]
    status, _h, _b = get(port, '/RomFS/nope.bin')
    assert status == 404


# ---- without --nxpk: the container endpoints do not exist ----

def test_no_container_means_no_stamp(pack_server, tmp_path, monkeypatch):
    pack = tmp_path / 'p'
    pack.mkdir()
    port = free_port()
    monkeypatch.setattr('sys.argv', ['pack_server.py', '--pack', str(pack), '--port', str(port)])
    threading.Thread(target=pack_server.main, daemon=True).start()
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=0.2):
                break
        except OSError:
            time.sleep(0.02)
    status, _h, _b = get(port, '/stamp')
    assert status == 404


def test_no_container_means_no_pack_endpoint(pack_server, tmp_path, monkeypatch):
    pack = tmp_path / 'p'
    pack.mkdir()
    port = free_port()
    monkeypatch.setattr('sys.argv', ['pack_server.py', '--pack', str(pack), '--port', str(port)])
    threading.Thread(target=pack_server.main, daemon=True).start()
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=0.2):
                break
        except OSError:
            time.sleep(0.02)
    status, _h, _b = get(port, '/pack.nxpk')
    assert status == 404


# ---- argument validation ----

def test_missing_pack_dir_exits(pack_server, tmp_path, monkeypatch):
    monkeypatch.setattr('sys.argv', ['pack_server.py', '--pack', str(tmp_path / 'nope')])
    with pytest.raises(SystemExit, match='no such pack'):
        pack_server.main()


def test_missing_manifest_exits(pack_server, tmp_path, monkeypatch):
    pack = tmp_path / 'p'
    pack.mkdir()
    monkeypatch.setattr('sys.argv', ['pack_server.py', '--pack', str(pack), '--manifest', str(tmp_path / 'nope.tsv')])
    with pytest.raises(SystemExit, match='no such manifest'):
        pack_server.main()


def test_missing_container_exits(pack_server, tmp_path, monkeypatch):
    pack = tmp_path / 'p'
    pack.mkdir()
    monkeypatch.setattr('sys.argv', ['pack_server.py', '--pack', str(pack), '--nxpk', str(tmp_path / 'nope.nxpk')])
    with pytest.raises(SystemExit, match='no such container'):
        pack_server.main()
