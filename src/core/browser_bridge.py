"""Optional loopback inbox; importing this module never opens a port."""
import json
import re
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from queue import Queue, Full, Empty
from threading import Thread, RLock
from urllib.parse import urlsplit
from src.core.browser_pairing import pairing_path, load_or_create_pairing, save_token, validate_port


def validate_capture(data):
    if not isinstance(data, dict) or data.get('type') not in ('m3u8', 'mp4'):
        raise ValueError('视频类型必须是 m3u8 或 mp4')
    result = {'type': data['type']}
    for key in ('url', 'referer'):
        value = data.get(key, '')
        if not isinstance(value, str) or len(value) > 16384 or any(c in value for c in '\r\n\x00'):
            raise ValueError('无效网址')
        if value or key == 'url':
            parsed = urlsplit(value)
            if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError('只支持 HTTP / HTTPS 网址')
            parsed.port  # 同时校验端口是否合法。
            value.encode('ascii')  # 浏览器提供编码后的原始 URL。
        result[key] = value
    return result


class BrowserBridge:
    def __init__(self, port=None, *, token_path=None, start=True):
        self.token_path = token_path if token_path is not None else pairing_path()
        self._pairing_lock = RLock()
        self.inbox = Queue(maxsize=10)
        self.token, saved_port = load_or_create_pairing(self.token_path)
        self.port = saved_port if port is None else port
        self.server = None
        self.thread = None
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass  # 不记录含签名的网址和配对码。

            def setup(self):
                super().setup()
                self.connection.settimeout(3)

            def reply(self, status, message):
                body = json.dumps({'message': message}, ensure_ascii=False).encode('utf-8')
                self.send_response(status)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                if self.headers.get('Host') != f'127.0.0.1:{self.server.server_port}':
                    return self.reply(403, '无效本地地址')
                origin = self.headers.get('Origin', '')
                if origin and not re.fullmatch(r'chrome-extension://[a-p]{32}', origin):
                    return self.reply(403, '只接受浏览器扩展')
                token = self.headers.get('X-AVDownloader-Token', '').encode('utf-8')
                if not secrets.compare_digest(token, bridge.token.encode('ascii')):
                    return self.reply(403, '配对码不正确，请重新配对')
                if self.path != '/capture':
                    return self.reply(404, '未知入口')
                if self.headers.get('Content-Type') != 'application/json' or self.headers.get('Transfer-Encoding'):
                    return self.reply(415, '需要 JSON')
                try:
                    size = int(self.headers.get('Content-Length', '0'))
                    if not 0 < size <= 40000:
                        return self.reply(413, '消息过大或为空')
                    capture = validate_capture(json.loads(self.rfile.read(size)))
                except (ValueError, OSError):
                    return self.reply(400, '无效视频地址')
                try:
                    with bridge._pairing_lock:
                        # 读取请求体期间可能发生重置，入队前再次校验。
                        if not secrets.compare_digest(token, bridge.token.encode('ascii')):
                            return self.reply(403, '配对码不正确，请重新配对')
                        bridge.inbox.put_nowait(capture)
                except Full:
                    return self.reply(429, '待确认地址过多，请先处理下载器中的弹窗')
                self.reply(202, '已发送，请在 AVDownloader 确认创建任务')

        self._handler = Handler
        if start:
            self.start()

    def start(self):
        if self.server is not None:
            return
        server = ThreadingHTTPServer(('127.0.0.1', self.port), self._handler)
        server.daemon_threads = True
        thread = Thread(target=server.serve_forever, name='browser-bridge', daemon=True)
        try:
            thread.start()
        except RuntimeError:
            server.server_close()
            raise
        self.server, self.thread = server, thread
        self.port = server.server_port

    def set_port(self, port):
        """先绑定并保存新端口，成功后切换；失败时保持原监听与配置。"""
        port = validate_port(port)
        if port == self.port and self.server is not None:
            return self.port
        server = ThreadingHTTPServer(('127.0.0.1', port), self._handler)
        server.daemon_threads = True
        thread = Thread(target=server.serve_forever, name='browser-bridge', daemon=True)
        with self._pairing_lock:
            try:
                # 成功启动新监听后再保存，保存失败会停止新监听。
                thread.start()
                save_token(self.token_path, self.token, port=port)
            except (OSError, ValueError, RuntimeError):
                if thread.is_alive():
                    server.shutdown()
                server.server_close()
                if thread.ident is not None:
                    thread.join(timeout=2)
                raise
            old_server, old_thread = self.server, self.thread
            self.server, self.thread, self.port = server, thread, port
        if old_server is not None:
            old_server.shutdown()
            old_server.server_close()
            old_thread.join(timeout=2)
        return self.port

    def reset_token(self):
        """重置浏览器扩展配对码，返回已保存的新码。

        保存成功后旧码失效，并清空尚未处理的视频地址队列；
        端口和监听服务保持不变，扩展需重新保存新码才能发送地址。
        保存失败则保留原配对码和队列。
        """
        with self._pairing_lock:
            token = secrets.token_urlsafe(32)
            save_token(self.token_path, token, port=self.port)
            self.token = token
            while True:
                try:
                    self.inbox.get_nowait()
                except Empty:
                    break
            return token

    def close(self):
        if self.server is None:
            return
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.server = None
        self.thread = None
