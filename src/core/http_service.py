"""Shared loopback HTTP listener for local integrations and encrypted playback."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import RLock, Thread


class LocalHTTPService:
    """Multiplexes local endpoints over one loopback port and reference-counts users."""

    def __init__(self, port=0, on_port_assigned=None):
        if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
            raise ValueError('本机服务端口必须是 0～65535 的整数。')
        self.port = port
        self.on_port_assigned = on_port_assigned
        self.browser_bridge = None
        self.playback_service = None
        self._users = set()
        self._lock = RLock()
        self.server = None
        self.thread = None

    def register_browser(self, bridge):
        self.browser_bridge = bridge

    def unregister_browser(self, bridge):
        if self.browser_bridge is bridge:
            self.browser_bridge = None

    def register_playback(self, playback):
        self.playback_service = playback

    def unregister_playback(self, playback):
        if self.playback_service is playback:
            self.playback_service = None

    def acquire(self, user):
        with self._lock:
            self._users.add(user)
            if self.server is not None:
                return self.port
            service = self

            class Handler(BaseHTTPRequestHandler):
                def log_message(self, *_args):
                    pass

                def reply(self, status, message):
                    import json
                    body = json.dumps({'message': message}, ensure_ascii=False).encode('utf-8')
                    self.send_response(status)
                    self.send_header('Content-Type', 'application/json; charset=utf-8')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

                def _range(self, size):
                    playback = service.playback_service
                    return (playback._handler._range(size, self.headers.get('Range'))
                            if playback is not None else None)

                def _serve(self, body):
                    playback = service.playback_service
                    if playback is None:
                        return self.send_error(404)
                    return playback._handler._serve(self, body)

                def do_POST(self):
                    bridge = service.browser_bridge
                    if bridge is not None and self.path.split('?', 1)[0] == '/capture':
                        bridge._handler.do_POST(self)
                    else:
                        self.send_error(404)

                def do_GET(self):
                    playback = service.playback_service
                    if playback is not None and self.path.startswith('/play/'):
                        self._serve(True)
                    else:
                        self.send_error(404)

                def do_HEAD(self):
                    playback = service.playback_service
                    if playback is not None and self.path.startswith('/play/'):
                        self._serve(False)
                    else:
                        self.send_error(404)

            preferred = self.port
            try:
                server = ThreadingHTTPServer(('127.0.0.1', preferred), Handler)
            except OSError:
                server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            actual = server.server_port
            try:
                if actual != preferred and self.on_port_assigned is not None:
                    self.on_port_assigned(actual)
                server.daemon_threads = True
                thread = Thread(target=server.serve_forever, name='local-http-service', daemon=True)
                thread.start()
            except Exception:
                server.server_close()
                self._users.discard(user)
                raise
            self.port = actual
            self.server, self.thread = server, thread
            return actual

    def release(self, user):
        with self._lock:
            self._users.discard(user)
            if self._users or self.server is None:
                return
            server, thread = self.server, self.thread
            self.server = self.thread = None
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    def get_status(self):
        with self._lock:
            return {'running': self.server is not None, 'address': '127.0.0.1',
                    'port': self.port, 'users': tuple(sorted(self._users))}

    def close(self):
        with self._lock:
            users = tuple(self._users)
        for user in users:
            self.release(user)
