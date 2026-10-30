"""A per-lease authenticating CONNECT relay, so each browser exits through its own owner's proxy.

Chrome's `--proxy-server` cannot carry a username and password: pointed at an
upstream that wants Basic auth it fails with ERR_PROXY_AUTH_UNSUPPORTED. OSLO
solved that with ONE relay (`oslo-proxy-relay.service`) reading ONE
`OSLO_PROXY_UPSTREAM`, which made every browser on the box exit through
whoever's subscription was in that file. Here each lease gets its own relay,
bound to loopback, carrying that lease's upstream in memory only. Stopping the
lease stops the relay; the credential is never written to disk or logged.

Only CONNECT is relayed (a browser tunnels every https:// request that way).
A plain-HTTP absolute-URI request is answered 405, matching OSLO's relay.
"""

import base64
import socket
import socketserver
import threading
from urllib.parse import unquote, urlsplit


def parse_upstream(upstream):
    """(host, port, auth_header_or_None) from `http://user:pass@host:port`."""
    raw = str(upstream or "").strip()
    if not raw:
        raise ValueError("upstream required")
    if "://" not in raw:
        raw = "http://" + raw
    parts = urlsplit(raw)
    if not parts.hostname or not parts.port:
        raise ValueError("upstream needs host and port")
    auth = None
    if parts.username is not None:
        cred = f"{unquote(parts.username)}:{unquote(parts.password or '')}"
        auth = "Basic " + base64.b64encode(cred.encode("utf-8")).decode("ascii")
    return parts.hostname, parts.port, auth


def _pipe(src, dst):
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        for s in (src, dst):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def _read_head(sock, limit=16384):
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            break
        buf += chunk
        if len(buf) > limit:
            break
    return buf


class _Handler(socketserver.BaseRequestHandler):
    def handle(self):
        client = self.request
        head = _read_head(client)
        line = head.split(b"\r\n", 1)[0].decode("latin-1", "replace")
        parts = line.split()
        if len(parts) < 2 or parts[0].upper() != "CONNECT":
            client.sendall(b"HTTP/1.1 405 Method Not Allowed\r\nContent-Length: 0\r\n\r\n")
            return
        target = parts[1]
        host, port, auth = self.server.upstream
        try:
            up = socket.create_connection((host, port), timeout=15)
        except OSError:
            client.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
            return
        req = f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n"
        if auth:
            req += f"Proxy-Authorization: {auth}\r\n"
        req += "\r\n"
        up.sendall(req.encode("latin-1"))
        resp = _read_head(up)
        status = resp.split(b"\r\n", 1)[0]
        if b" 200" not in status:
            # Pass the upstream's verdict through (407 etc.), never its body.
            code = status.split()[1] if len(status.split()) > 1 else b"502"
            client.sendall(b"HTTP/1.1 " + code + b" Upstream Refused\r\nContent-Length: 0\r\n\r\n")
            up.close()
            return
        client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        # Bytes that arrived behind either head belong to the tunnel.
        early_up = resp.split(b"\r\n\r\n", 1)[1] if b"\r\n\r\n" in resp else b""
        early_client = head.split(b"\r\n\r\n", 1)[1] if b"\r\n\r\n" in head else b""
        if early_up:
            client.sendall(early_up)
        if early_client:
            up.sendall(early_client)
        up.settimeout(None)
        t = threading.Thread(target=_pipe, args=(up, client), daemon=True)
        t.start()
        _pipe(client, up)
        t.join(timeout=1)


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


class LeaseProxyRelay:
    """One relay for one lease. `server` is what Chrome's --proxy-server gets."""

    def __init__(self, upstream, bind="127.0.0.1", port=0):
        self._srv = _Server((bind, port), _Handler)
        self._srv.upstream = parse_upstream(upstream)
        self.bind, self.port = self._srv.server_address[:2]
        self._thread = threading.Thread(target=self._srv.serve_forever, daemon=True)

    @property
    def server(self):
        return f"http://{self.bind}:{self.port}"

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._srv.shutdown()
        self._srv.server_close()
        # Drop the credential with the relay.
        self._srv.upstream = None

    def __repr__(self):  # never print the upstream
        return f"LeaseProxyRelay({self.server})"
