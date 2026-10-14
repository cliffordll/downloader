"""Optional loopback inbox; importing this module never opens a port."""
import json
import re
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from queue import Queue, Full, Empty
from threading import Thread, RLock
from urllib.parse import urlsplit
from src.core.browser_pairing import pairing_path, load_or_create_token, save_token


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
    def __init__(self, port=17839, *, token_path=None):
        self.token_path = token_path if token_path is not None else pairing_path()
        self._pairing_lock = RLock()
        self.inbox = Queue(maxsize=10)
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
                if self.headers.get('Host') != f'127.0.0.1:{bridge.port}':
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

        self.server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
        try:
            self.token = load_or_create_token(self.token_path)
        except (OSError, ValueError):
            self.server.server_close()
            raise
        self.server.daemon_threads = True
        self.port = self.server.server_port
        self.thread = Thread(target=self.server.serve_forever, name='browser-bridge', daemon=True)
        self.thread.start()

    def reset_token(self):
        with self._pairing_lock:
            token = secrets.token_urlsafe(32)
            save_token(self.token_path, token)
            self.token = token
            while True:
                try:
                    self.inbox.get_nowait()
                except Empty:
                    break
            return token

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
