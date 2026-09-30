"""本地 HTTP 集成测试：校验真实 Range 请求和落盘内容，不访问外网。"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import patch

from src.core.sys_setting import SysSetting
from src.media.downloader import Downloader
from src.media.mp4.mp4_downloader import MP4Downloader
from src.core.task_service import TaskService
from src.schemas.task import TaskStatus, TaskProgress
from src.storage.task_repository import TaskRepository


DATA = bytes(range(256)) * 8192

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        server = self.server
        server.received.append(dict(self.headers))
        mode = server.mode
        if mode == 'fail':
            self.send_response(503)
            self.end_headers()
            return
        if mode == 'html':
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            self.wfile.write(b'<html>login</html>')
            return
        raw_range = self.headers.get('Range')
        offset = int(raw_range.split('=')[1].split('-')[0]) if raw_range else 0
        if mode == '416' and raw_range:
            self.send_response(416)
            self.send_header('Content-Range', f'bytes */{len(DATA)}')
            self.end_headers()
            return
        ranged = raw_range and mode not in ('ignore', 'unknown')
        if self.headers.get('If-Range') != '"v1"':
            ranged = False
        offset = offset if ranged else 0
        self.send_response(206 if ranged else 200)
        self.send_header('Content-Type', 'video/mp4')
        self.send_header('ETag', '"v2"' if mode == 'changed' and ranged else '"v1"')
        if ranged:
            start = offset + 1 if mode == 'bad-range' else offset
            self.send_header('Content-Range', f'bytes {start}-{len(DATA)-1}/{len(DATA)}')
        if mode != 'unknown':
            self.send_header('Content-Length', str(len(DATA) - offset))
        self.end_headers()
        try:
            for start in range(offset, len(DATA), 16384):
                self.wfile.write(DATA[start:start + 16384])
                self.wfile.flush()
                if mode == 'slow':
                    time.sleep(0.005)
                if mode == 'truncate' and start >= offset + 65536:
                    self.close_connection = True
                    break
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass


class MP4DownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.repository = TaskRepository(self.root / 'downloads.db')
        self.service = TaskService(self.repository)
        self.engine = MP4Downloader(self.repository)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.mode = 'normal'
        self.server.received = []
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.config = patch.object(SysSetting, '_values', dict(SysSetting.Defaults(),
            request_interval=0, max_retries=0, connect_timeout=1, read_timeout=1))
        self.config.start()
        self.rate = patch.object(Downloader, '_next_request', 0)
        self.rate.start()
        self.cooldown = patch.object(Downloader, '_paused_until', 0)
        self.cooldown.start()

    def tearDown(self):
        self.engine.shutdown()
        self.engine._pool.shutdown(wait=True)
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.cooldown.stop()
        self.rate.stop()
        self.config.stop()
        self.temp.cleanup()

    def create(self, name='video'):
        return self.service.create_mp4(self.root / name,
            f'http://127.0.0.1:{self.server.server_port}/video.mp4')

    def wait(self, condition):
        limit = time.monotonic() + 6
        while not condition():
            if time.monotonic() > limit:
                self.fail('后台下载未在限定时间内完成')
            time.sleep(0.01)

    def run_task(self, task):
        self.assertTrue(self.engine.start(task.id))
        self.wait(lambda: not self.engine.busy(task.id))
        return self.repository.get(task.id)

    def seed_partial(self, task, size=1000, validator=True):
        (task.save_dir / task.details.temporary_path).write_bytes(DATA[:size])
        def update(record):
            record.details.etag = '"v1"' if validator else None
            record.progress = TaskProgress(downloaded_bytes=size, total_bytes=len(DATA))
        self.repository.mutate(task.id, update)

    def assert_complete(self, task):
        record = self.repository.get(task.id)
        self.assertEqual(record.status, TaskStatus.COMPLETED, record.last_error)
        self.assertEqual((task.save_dir / task.details.target_path).read_bytes(), DATA)
        self.assertFalse((task.save_dir / task.details.temporary_path).exists())
        self.assertEqual(record.progress.downloaded_bytes, len(DATA))
        self.assertEqual(record.percent, 100)

    def test_download_and_unknown_size(self):
        for mode in ('normal', 'unknown'):
            self.server.mode = mode
            task = self.create(mode)
            self.run_task(task)
            self.assert_complete(task)

    def test_resume_uses_range_and_validator(self):
        task = self.create()
        self.seed_partial(task)
        self.run_task(task)
        self.assertEqual(self.server.received[0]['Range'], 'bytes=1000-')
        self.assertEqual(self.server.received[0]['If-Range'], '"v1"')
        self.assert_complete(task)

    def test_ignored_range_changed_file_and_416_restart_cleanly(self):
        for mode in ('ignore', 'changed', '416', 'bad-range'):
            self.server.mode = mode
            task = self.create(mode)
            self.seed_partial(task)
            self.run_task(task)
            self.assert_complete(task)

    def test_no_validator_restarts_instead_of_appending(self):
        task = self.create()
        self.seed_partial(task, validator=False)
        self.run_task(task)
        self.assertNotIn('Range', self.server.received[0])
        self.assert_complete(task)

    def test_pause_duplicate_start_and_restart_resume(self):
        self.server.mode = 'slow'
        task = self.create()
        path = task.save_dir / task.details.temporary_path
        self.engine.start(task.id)
        self.assertFalse(self.engine.start(task.id))
        self.wait(lambda: path.exists() and path.stat().st_size >= 65536)
        self.engine.pause(task.id)
        self.wait(lambda: not self.engine.busy(task.id))
        record = self.repository.get(task.id)
        self.assertEqual(record.status, TaskStatus.PAUSED)
        self.assertEqual(record.progress.downloaded_bytes, path.stat().st_size)
        self.assertFalse((task.save_dir / task.details.target_path).exists())
        self.engine.shutdown()
        self.engine._pool.shutdown(wait=True)
        self.engine = MP4Downloader(TaskRepository(self.repository.path))
        TaskService(self.repository).load_tasks()
        self.run_task(task)
        self.assert_complete(task)

    def test_truncated_response_is_failed_and_partial_kept(self):
        self.server.mode = 'truncate'
        task = self.create()
        record = self.run_task(task)
        self.assertEqual(record.status, TaskStatus.FAILED)
        self.assertGreater(record.progress.downloaded_bytes, 0)
        self.assertFalse((task.save_dir / task.details.target_path).exists())
        self.server.mode = 'normal'
        self.run_task(task)
        self.assert_complete(task)

    def test_html_is_not_saved_as_video(self):
        self.server.mode = 'html'
        task = self.create()
        self.assertEqual(self.run_task(task).status, TaskStatus.FAILED)
        self.assertFalse((task.save_dir / task.details.target_path).exists())

    def test_retry_is_bounded(self):
        self.server.mode = 'fail'
        SysSetting._values['max_retries'] = 1
        task = self.create()
        self.assertEqual(self.run_task(task).status, TaskStatus.FAILED)
        self.assertEqual(len(self.server.received), 2)

    def test_shutdown_and_finalization_recovery(self):
        self.server.mode = 'slow'
        task = self.create()
        path = task.save_dir / task.details.temporary_path
        self.engine.start(task.id)
        self.wait(lambda: path.exists() and path.stat().st_size >= 65536)
        self.engine.shutdown()
        self.wait(lambda: not self.engine.busy(task.id))
        self.assertEqual(self.repository.get(task.id).status, TaskStatus.INTERRUPTED)
        target = task.save_dir / task.details.target_path
        target.write_bytes(DATA)
        self.repository.mutate(task.id, lambda r: setattr(r.details, 'verified_bytes', len(DATA)))
        TaskService(self.repository).load_tasks()
        self.assertEqual(self.repository.get(task.id).status, TaskStatus.COMPLETED)

    def test_existing_target_is_preserved(self):
        task = self.create()
        target = task.save_dir / task.details.target_path
        target.write_bytes(b'existing')
        self.assertEqual(self.run_task(task).status, TaskStatus.FAILED)
        self.assertEqual(target.read_bytes(), b'existing')

    def test_mp4_waits_for_shared_slot_and_queued_pause(self):
        SysSetting._values['max_workers'] = 1
        Downloader.AcquireTransfer(lambda: None)  # 模拟一个 TS 正占用全局额度。
        try:
            task = self.create()
            self.engine.start(task.id)
            time.sleep(0.15)
            self.assertEqual(self.server.received, [])
            self.engine.pause(task.id)
            self.wait(lambda: not self.engine.busy(task.id))
            self.assertEqual(self.repository.get(task.id).status, TaskStatus.PAUSED)
        finally:
            Downloader.ReleaseTransfer()
        self.run_task(task)
        self.assert_complete(task)
