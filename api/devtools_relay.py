"""
A TCP relay from a reachable port to the adb-forwarded DevTools socket.

`adb forward tcp:9222 localabstract:chrome_devtools_remote` listens on the
adb server's loopback only, so a controller on another host cannot reach it.
The Control API binds a relay on its own interface and copies bytes both ways;
Chrome builds the `webSocketDebuggerUrl` in `/json/version` from the Host
header it receives, so a client that connects through the relay gets a URL it
can also open through the relay. Threads, not asyncio: the Control API is a
threaded Flask app and the relay carries one or two connections.
"""

import socket
import threading


class DevtoolsRelay:
    def __init__(self, listen_host, listen_port, target_host, target_port):
        self.listen_host = listen_host
        self.listen_port = int(listen_port)
        self.target_host = target_host
        self.target_port = int(target_port)
        self._server = None
        self._thread = None
        self._stopped = threading.Event()
        self.connections = 0

    @property
    def running(self):
        return self._server is not None and not self._stopped.is_set()

    def start(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.listen_host, self.listen_port))
        srv.listen(8)
        srv.settimeout(0.5)
        self._server = srv
        self.listen_port = srv.getsockname()[1]
        self._thread = threading.Thread(target=self._accept_loop, name="devtools-relay", daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._stopped.set()
        srv, self._server = self._server, None
        if srv is not None:
            try:
                srv.close()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    def _accept_loop(self):
        while not self._stopped.is_set():
            srv = self._server
            if srv is None:
                return
            try:
                client, _ = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            self.connections += 1
            threading.Thread(target=self._bridge, args=(client,), daemon=True).start()

    def _bridge(self, client):
        upstream = None
        try:
            upstream = socket.create_connection((self.target_host, self.target_port), timeout=5)
            upstream.settimeout(None)
            client.settimeout(None)
            a = threading.Thread(target=self._pump, args=(client, upstream), daemon=True)
            b = threading.Thread(target=self._pump, args=(upstream, client), daemon=True)
            a.start()
            b.start()
            a.join()
            b.join()
        except OSError:
            pass
        finally:
            for s in (client, upstream):
                if s is not None:
                    try:
                        s.close()
                    except OSError:
                        pass

    @staticmethod
    def _pump(src, dst):
        try:
            while True:
                chunk = src.recv(65536)
                if not chunk:
                    break
                dst.sendall(chunk)
        except OSError:
            pass
        finally:
            try:
                dst.shutdown(socket.SHUT_WR)
            except OSError:
                pass


def start_relay(listen_host, listen_port, target_host, target_port):
    return DevtoolsRelay(listen_host, listen_port, target_host, target_port).start()
