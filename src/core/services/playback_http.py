"""Multiplexed loopback HTTP playback for encrypted MP4 sessions."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import re
import secrets
from threading import Event, Lock, Thread
import time
from urllib.parse import quote, urlsplit

from src.media.mp4.encrypted_file import EncryptedMP4Reader


class PlaybackHTTPServer:
    """One loopback listener that serves independently expiring playback sessions."""

    def __init__(self, port=64676, idle_timeout=30 * 60, on_port_assigned=None, http_service=None):
        if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
            raise ValueError('加密视频播放服务端口必须是 0～65535 的整数。')
        self.configured_port = port
        self._on_port_assigned = on_port_assigned
        self.http_service = http_service
        self.idle_timeout = idle_timeout
        self._sessions = {}
        self._lock = Lock()
        self._server = None
        self._thread = None
        self._closed = Event()
        self._reaper = Thread(target=self._reap_idle_sessions,
                              name='encrypted-mp4-session-reaper', daemon=True)
        self._reaper.start()

    def _create_server(self):
        service = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *_args):
                pass

            def _range(self, size):
                value = self.headers.get('Range')
                if not value:
                    return 0, size, False
                match = re.fullmatch(r'bytes=(\d*)-(\d*)', value.strip())
                if not match or not any(match.groups()) or size == 0:
                    return None
                first, last = match.groups()
                if first:
                    start = int(first)
                    end = min(int(last), size - 1) + 1 if last else size
                else:
                    suffix = int(last)
                    if suffix <= 0:
                        return None
                    start, end = max(0, size - suffix), size
                if start >= size or start >= end:
                    return None
                return start, end, True

            def _serve(self, body):
                expected_host = f'127.0.0.1:{self.server.server_port}'
                parts = urlsplit(self.path).path.split('/')
                token = parts[2] if len(parts) == 4 and parts[1] == 'play' else None
                with service._lock:
                    session = service._sessions.get(token) if token else None
                    if (not session or self.headers.get('Host') != expected_host
                            or urlsplit(self.path).path != session['url_path']
                            or urlsplit(self.path).query):
                        session = None
                    else:
                        session['last_activity'] = time.monotonic()
                if session is None:
                    self.send_error(404)
                    return
                try:
                    reader = EncryptedMP4Reader(session['path'], session['wrapping_key'])
                except (OSError, ValueError):
                    self.send_error(404)
                    return
                selected = self._range(reader.size)
                if selected is None:
                    self.send_response(416)
                    self.send_header('Content-Range', f'bytes */{reader.size}')
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
                start, end, partial = selected
                self.send_response(206 if partial else 200)
                self.send_header('Content-Type', 'video/mp4')
                self.send_header('Accept-Ranges', 'bytes')
                self.send_header('Content-Length', str(end - start))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Connection', 'close')
                if partial:
                    self.send_header('Content-Range', f'bytes {start}-{end - 1}/{reader.size}')
                self.end_headers()
                if body:
                    try:
                        cursor = start
                        while cursor < end:
                            chunk_end = min(cursor + 256 * 1024, end)
                            data = reader.read_range(cursor, chunk_end)
                            self.wfile.write(data)
                            cursor = chunk_end
                            with service._lock:
                                active = service._sessions.get(token)
                                if active is session:
                                    active['last_activity'] = time.monotonic()
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        pass
                self.close_connection = True

            def do_GET(self):
                self._serve(True)

            def do_HEAD(self):
                self._serve(False)

        self._handler = Handler
        if self.http_service is not None:
            self.http_service.register_playback(self)
            assigned_port = self.http_service.acquire('playback')
            try:
                if self._on_port_assigned is not None:
                    self._on_port_assigned(assigned_port)
            except Exception:
                self.http_service.release('playback')
                self.http_service.unregister_playback(self)
                raise
            self.configured_port = assigned_port
            return self.http_service.server, self.http_service.thread

        preferred_port = self.configured_port
        try:
            server = ThreadingHTTPServer(('127.0.0.1', preferred_port), Handler)
        except OSError:
            server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        assigned_port = server.server_port
        try:
            if assigned_port != preferred_port:
                self.configured_port = assigned_port
                if self._on_port_assigned is not None:
                    self._on_port_assigned(assigned_port)
        except Exception:
            server.server_close()
            self.configured_port = preferred_port
            raise
        server.daemon_threads = True
        thread = Thread(target=server.serve_forever, name='encrypted-mp4-playback', daemon=True)
        try:
            thread.start()
        except RuntimeError:
            server.server_close()
            raise
        return server, thread

    def create_session(self, path, wrapping_key, display_name=None):
        with self._lock:
            if self._closed.is_set():
                raise RuntimeError('加密视频播放服务已关闭')
            if self._server is None:
                self._server, self._thread = self._create_server()
            token = secrets.token_urlsafe(32)
            display_name = display_name or getattr(path, 'name', 'video.mp4')
            if display_name.lower().endswith('.enc'):
                display_name = display_name[:-4]
            url_path = f'/play/{token}/{quote(display_name, safe="")}'
            self._sessions[token] = {
                'path': str(path),
                'wrapping_key': bytes(wrapping_key),
                'url_path': url_path,
                'last_activity': time.monotonic(),
            }
            return token, f'http://127.0.0.1:{self._server.server_port}{url_path}'

    def close_session(self, token):
        with self._lock:
            session = self._sessions.pop(token, None)
            if session:
                session['wrapping_key'] = b''

    def get_status(self):
        with self._lock:
            if self._server is None:
                return {'running': False, 'address': None, 'port': None,
                        'configured_port': self.configured_port, 'sessions': 0}
            return {
                'running': True,
                'address': '127.0.0.1',
                'port': self._server.server_port,
                'configured_port': self.configured_port,
                'sessions': len(self._sessions),
            }

    def _reap_idle_sessions(self):
        interval = min(30, max(1, self.idle_timeout / 4))
        while not self._closed.wait(interval):
            with self._lock:
                now = time.monotonic()
                expired = [token for token, session in self._sessions.items()
                           if now - session['last_activity'] >= self.idle_timeout]
                for token in expired:
                    self._sessions.pop(token)['wrapping_key'] = b''
                server = self._server if not self._sessions else None
                thread = self._thread if server is not None else None
                if server is not None:
                    self._server = None
                    self._thread = None
            if self.http_service is not None:
                if server is not None:
                    self.http_service.release('playback')
            else:
                self._stop_server(server, thread)

    @staticmethod
    def _stop_server(server, thread):
        if server is None:
            return
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    def close(self):
        self._closed.set()
        with self._lock:
            for session in self._sessions.values():
                session['wrapping_key'] = b''
            self._sessions.clear()
            server, thread = self._server, self._thread
            self._server = None
            self._thread = None
        if self.http_service is not None:
            if server is not None:
                self.http_service.release('playback')
            self.http_service.unregister_playback(self)
        else:
            self._stop_server(server, thread)
        self._reaper.join(timeout=2)
