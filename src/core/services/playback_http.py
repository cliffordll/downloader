"""Multiplexed loopback HTTP playback for encrypted MP4 and M3U8 tasks."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import mimetypes
import posixpath
import re
import secrets
from threading import Event, Lock, Thread
import time
from urllib.parse import quote, unquote, urlsplit

from src.core.task_files import playlist_path, segment_path
from src.media.encrypted_file import EncryptedMP4Reader
from src.schemas.task import M3U8Task


class PlaybackHTTPServer:
    """One loopback listener serving independently expiring encrypted task sessions."""

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
                              name='encrypted-media-session-reaper', daemon=True)
        self._reaper.start()

    def _create_server(self):
        service = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *_args):
                pass

            @staticmethod
            def _range(size, value):
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

            def _lookup(self):
                request = urlsplit(self.path)
                parts = request.path.split('/')
                expected_host = f'127.0.0.1:{self.server.server_port}'
                if (self.headers.get('Host') != expected_host or request.query or request.fragment
                        or len(parts) < 4 or parts[1] != 'play'):
                    return None, None, None
                token = parts[2]
                try:
                    resource_name = unquote('/'.join(parts[3:]), errors='strict')
                except (UnicodeDecodeError, ValueError):
                    return None, None, None
                normalized = posixpath.normpath(resource_name)
                if (not resource_name or resource_name.startswith('/') or normalized in ('.', '..')
                        or normalized.startswith('../') or '\\' in resource_name or '\x00' in resource_name):
                    return None, None, None
                with service._lock:
                    session = service._sessions.get(token)
                    resource = session['resources'].get(normalized) if session else None
                    if session is None or resource is None:
                        return None, None, None
                    session['last_activity'] = time.monotonic()
                    return token, session, resource

            def _rewrite_playlist(self, token, session, resource_name, content):
                try:
                    text = content.decode('utf-8-sig')
                except UnicodeDecodeError as error:
                    raise ValueError('加密 M3U8 播放列表不是有效的 UTF-8 文本。') from error
                base = posixpath.dirname(resource_name)

                def local_url(uri):
                    parsed = urlsplit(uri.strip())
                    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
                        raise ValueError('本地播放列表包含非本地资源地址。')
                    decoded = unquote(parsed.path, errors='strict')
                    target = posixpath.normpath(posixpath.join(base, decoded))
                    if (not decoded or target in ('.', '..') or target.startswith('../')
                            or target.startswith('/') or target not in session['resources']):
                        raise ValueError(f'播放列表引用了未登记的任务资源：{uri}')
                    escaped = '/'.join(quote(part, safe='') for part in target.split('/'))
                    return f'{Handler._play_base(self, token)}/{escaped}'

                rewritten = []
                segment_block = []
                for line in text.splitlines():
                    stripped = line.strip()
                    if not stripped:
                        if segment_block:
                            segment_block.append(line)
                        else:
                            rewritten.append(line)
                    elif stripped.startswith('#'):
                        rewritten_line = re.sub(
                            r'URI="([^"]+)"', lambda match: f'URI="{local_url(match.group(1))}"', line)
                        if segment_block or stripped.startswith('#EXTINF:'):
                            segment_block.append(rewritten_line)
                        else:
                            rewritten.append(rewritten_line)
                    else:
                        base_path = posixpath.dirname(resource_name)
                        decoded = unquote(urlsplit(stripped).path, errors='strict')
                        target = posixpath.normpath(posixpath.join(base_path, decoded))
                        if target in session['missing_resources']:
                            # Keep partial encrypted downloads playable by omitting the
                            # playlist metadata and URI for segments not downloaded yet.
                            segment_block.clear()
                            continue
                        rewritten_uri = local_url(stripped)
                        rewritten.extend(segment_block)
                        segment_block.clear()
                        rewritten.append(rewritten_uri)
                # Ignore a trailing metadata block if its segment URI was never written.
                return ('\n'.join(rewritten) + '\n').encode('utf-8')

            def _play_base(self, token):
                return f'http://127.0.0.1:{self.server.server_port}/play/{token}'

            def _serve(self, body):
                token, session, resource = Handler._lookup(self)
                if session is None:
                    self.send_error(404)
                    return
                try:
                    reader = EncryptedMP4Reader(resource['path'], session['wrapping_key'])
                    if resource.get('playlist'):
                        payload = Handler._rewrite_playlist(self,
                            token, session, resource['route'], reader.read_range(0, reader.size))
                        self.send_response(200)
                        self.send_header('Content-Type', 'application/vnd.apple.mpegurl; charset=utf-8')
                        self.send_header('Content-Length', str(len(payload)))
                        self.send_header('Cache-Control', 'no-store')
                        self.send_header('Connection', 'close')
                        self.end_headers()
                        if body:
                            self.wfile.write(payload)
                        self.close_connection = True
                        return
                    if resource['content_type'] == 'video/mp4':
                        selected = Handler._range(reader.size, self.headers.get('Range'))
                    else:
                        selected = (0, reader.size, False)
                except (OSError, ValueError):
                    self.send_error(404)
                    return
                if selected is None:
                    self.send_response(416)
                    self.send_header('Content-Range', f'bytes */{reader.size}')
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
                start, end, partial = selected
                self.send_response(206 if partial else 200)
                self.send_header('Content-Type', resource['content_type'])
                self.send_header('Accept-Ranges', 'bytes' if resource['content_type'] == 'video/mp4' else 'none')
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
                            self.wfile.write(reader.read_range(cursor, chunk_end))
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
        thread = Thread(target=server.serve_forever, name='encrypted-media-playback', daemon=True)
        try:
            thread.start()
        except RuntimeError:
            server.server_close()
            raise
        return server, thread

    @staticmethod
    def _content_type(resource_name):
        suffix = resource_name.lower().rsplit('.', 1)[-1] if '.' in resource_name else ''
        return {'m3u8': 'application/vnd.apple.mpegurl', 'ts': 'video/mp2t',
                'key': 'application/octet-stream', 'mp4': 'video/mp4'}.get(
                    suffix, mimetypes.guess_type(resource_name)[0] or 'application/octet-stream')

    @staticmethod
    def _logical_name(path_name):
        return path_name[:-4] if path_name.lower().endswith('.enc') else path_name

    def _new_session(self, wrapping_key, resources, *, task_id=None, task_name=None,
                     missing_resources=()):
        with self._lock:
            if self._closed.is_set():
                raise RuntimeError('加密视频播放服务已关闭')
            if self._server is None:
                self._server, self._thread = self._create_server()
            token = secrets.token_urlsafe(32)
            safe_resources = {}
            for route, metadata in resources.items():
                route = route.replace('\\', '/')
                normalized = posixpath.normpath(route)
                if (route.startswith('/') or normalized in ('.', '..') or normalized.startswith('../')
                        or '\x00' in route):
                    raise ValueError('播放资源路径无效。')
                safe_resources[normalized] = {**metadata, 'route': normalized}
            self._sessions[token] = {
                'task_id': str(task_id) if task_id is not None else None,
                'task_name': task_name,
                'wrapping_key': bytes(wrapping_key),
                'resources': safe_resources,
                'missing_resources': set(missing_resources),
                'last_activity': time.monotonic(),
            }
            return token

    def create_session(self, path, wrapping_key, display_name=None, *, task_id=None, task_name=None):
        """Create an MP4 session; route is /play/<token>/<task resource path>."""
        display_name = display_name or getattr(path, 'name', 'video.mp4')
        resource_name = self._logical_name(display_name)
        token = self._new_session(wrapping_key, {resource_name: {
            'path': str(path), 'content_type': 'video/mp4'}}, task_id=task_id, task_name=task_name)
        route = '/'.join(quote(part, safe='') for part in resource_name.split('/'))
        url_path = f'/play/{token}/{route}'
        return token, f'http://127.0.0.1:{self._server.server_port}{url_path}'

    def create_m3u8_session(self, task, wrapping_key):
        """Serve an encrypted task's original playlist name and all registered resources."""
        if not isinstance(task, M3U8Task) or not task.details.playlist_path:
            raise ValueError('M3U8 任务缺少播放列表路径。')
        prefix = task.name
        playlist_disk = playlist_path(task)
        playlist_logical = self._logical_name(task.details.playlist_path)
        resources = {f'{prefix}/{playlist_logical}': {
            'path': str(playlist_disk), 'content_type': 'application/vnd.apple.mpegurl',
            'playlist': True}}
        missing_resources = set()
        for segment in task.details.media_segments:
            disk = segment_path(task, segment)
            logical = self._logical_name(segment.segment_path)
            route = f'{prefix}/{logical}'
            if disk.is_file():
                resources[route] = {'path': str(disk), 'content_type': self._content_type(logical)}
            else:
                missing_resources.add(route)
        if len(resources) == 1:
            raise FileNotFoundError('没有已下载完成的加密分片，暂时无法播放。')
        token = self._new_session(wrapping_key, resources, task_id=task.id,
                                  task_name=task.name, missing_resources=missing_resources)
        route = '/'.join(quote(part, safe='') for part in f'{prefix}/{playlist_logical}'.split('/'))
        return token, f'http://127.0.0.1:{self._server.server_port}/play/{token}/{route}'

    def close_session(self, token):
        with self._lock:
            session = self._sessions.pop(token, None)
            if session:
                session['wrapping_key'] = b''

    def close_task_sessions(self, task_id):
        """Revoke all active URLs that were issued for one task."""
        task_id = str(task_id)
        with self._lock:
            tokens = [token for token, session in self._sessions.items()
                      if session['task_id'] == task_id]
            for token in tokens:
                self._sessions.pop(token)['wrapping_key'] = b''
        return tokens

    def get_status(self):
        with self._lock:
            if self._server is None:
                return {'running': False, 'address': None, 'port': None,
                        'configured_port': self.configured_port, 'sessions': 0}
            return {'running': True, 'address': '127.0.0.1', 'port': self._server.server_port,
                    'configured_port': self.configured_port, 'sessions': len(self._sessions)}

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
