"""不运行 wx 事件循环，验证分片下载在后台独立落库及异常恢复。"""
from http.server import ThreadingHTTPServer
from pathlib import Path
from queue import Queue, SimpleQueue
import sqlite3
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import patch

import requests

from src.core.sys_setting import SysSetting
from src.core.task_service import TaskService
from src.media.downloader import Downloader
from src.media.m3u8.m3u8_downloader import M3U8Downloader as Engine
from src.schemas.task import TaskStatus, FileStatus
from src.storage.task_repository import TaskRepository
from test_mp4_download import Handler, DATA


class M3U8DownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.repository = TaskRepository(self.root / 'downloads.db')
        self.service = TaskService(self.repository)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.mode, self.server.received = 'normal', []
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.patchers = []
        state = dict(_jobs={}, _pending=set(), _requesting=set(), _failed=set(), _errors={},
                     _paused_files=set(), _shutdown=threading.Event(), _user_paused=threading.Event(),
                     _changes=SimpleQueue(), errors=SimpleQueue(), _unsaved={}, _storage_error='',
                     threadQueue=Queue(), isStop=True, _master=None)
        for owner, values in ((Engine, state), (Downloader, dict(_next_request=0, _paused_until=0)),
                              (SysSetting, dict(_values=dict(SysSetting.Defaults(), max_workers=1,
                               max_retries=0, request_interval=0, connect_timeout=1, read_timeout=1)))):
            for name, value in values.items():
                patcher = patch.object(owner, name, value)
                patcher.start()
                self.patchers.append(patcher)

    def tearDown(self):
        Engine.Shutdown()
        if Engine._master:
            Engine._master.join(5)
            self.assertFalse(Engine._master.is_alive())
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp.cleanup()

    def create(self, count=1):
        return self.service.create_m3u8(self.root / 'video',
            f'http://127.0.0.1:{self.server.server_port}/index.m3u8',
            '#EXTM3U\n' + ''.join(f'#EXTINF:4,\n{i}.ts\n' for i in range(count)))

    def wait(self, condition):
        deadline = time.monotonic() + 6
        while not condition():
            if time.monotonic() > deadline:
                self.fail('后台状态未在限定时间内完成')
            time.sleep(0.01)

    def finish(self):
        self.wait(lambda: Engine.isStop)
        Engine._master.join(1)

    def test_completion_is_saved_without_gui_and_segments_are_streamed(self):
        task = self.create(3)
        original = requests.get
        workers = []
        save = self.service.finish_segment

        def record_thread(*args, **kwargs):
            workers.append(threading.get_ident())
            return save(*args, **kwargs)

        def get(*args, **kwargs):
            self.assertTrue(kwargs['stream'])
            response = original(*args, **kwargs)
            # requests.content 会完整缓存响应；这里始终禁止访问，防止流式下载退化。
            class StreamOnly(requests.Response):
                @property
                def content(self):
                    raise AssertionError('分片不能整块读入内存')
            response.__class__ = StreamOnly
            return response

        with patch('requests.get', side_effect=get), patch.object(self.service, 'finish_segment', side_effect=record_thread):
            self.assertEqual(Engine.StartTask(self.service, task.id, range(3)), 3)
            self.finish()
        saved = self.repository.get(task.id)
        self.assertEqual(saved.status, TaskStatus.WAITING_MERGE)
        self.assertEqual(saved.progress.completed_segments, 3)
        self.assertEqual(saved.progress.downloaded_bytes, len(DATA) * 3)
        self.assertTrue(all(worker != threading.get_ident() for worker in workers))
        for segment in saved.details.segments:
            self.assertEqual((task.save_dir / segment.relative_path).read_bytes(), DATA)
        self.assertEqual(Engine.changes(), {task.id})
        self.assertFalse(Engine.IsBusy())
        self.assertEqual(list(task.save_dir.rglob('*.part')), [])

    def test_failed_requests_stop_at_retry_limit_and_keep_error(self):
        task = self.create()
        self.server.mode = 'fail'
        SysSetting._values['max_retries'] = 1
        Engine.StartTask(self.service, task.id, [0])
        self.finish()
        saved = self.repository.get(task.id)
        self.assertEqual(len(self.server.received), 2)
        self.assertEqual(saved.status, TaskStatus.FAILED)
        self.assertIn('503', saved.details.segments[0].last_error)
        self.assertEqual(list(task.save_dir.rglob('*.part')), [])

    def test_truncated_stream_does_not_publish_incomplete_segment(self):
        task = self.create()
        self.server.mode = 'truncate'
        Engine.StartTask(self.service, task.id, [0])
        self.finish()
        saved = self.repository.get(task.id)
        self.assertEqual(saved.status, TaskStatus.FAILED)
        self.assertEqual(saved.progress.completed_segments, 0)
        self.assertFalse((task.save_dir / task.details.segments[0].relative_path).exists())
        self.assertEqual(list(task.save_dir.rglob('*.part')), [])

    def test_pause_and_resume_are_persisted_without_gui_polling(self):
        task = self.create(2)
        self.server.mode = 'slow'
        Engine.StartTask(self.service, task.id, [0, 1])
        self.wait(lambda: bool(self.server.received))
        Engine.Pause()
        self.wait(lambda: self.repository.get(task.id).status == TaskStatus.PAUSING)
        self.wait(lambda: self.repository.get(task.id).status == TaskStatus.PAUSED)
        self.assertEqual(len(self.server.received), 1)
        self.assertEqual(self.repository.get(task.id).progress.completed_segments, 1)
        Engine.Resume()
        self.finish()
        self.assertEqual(len(self.server.received), 2)
        self.assertEqual(self.repository.get(task.id).status, TaskStatus.WAITING_MERGE)

    def test_shutdown_discards_partial_file_and_saves_interruption(self):
        task = self.create(2)
        self.server.mode = 'slow'
        Engine.StartTask(self.service, task.id, [0, 1])
        self.wait(lambda: bool(self.server.received))
        Engine.Shutdown()
        self.finish()
        saved = self.repository.get(task.id)
        self.assertEqual(saved.status, TaskStatus.INTERRUPTED)
        self.assertEqual(saved.progress.completed_segments, 0)
        self.assertEqual(len(self.server.received), 1)
        self.assertEqual(list(task.save_dir.rglob('*.part')), [])

    def test_database_failure_pauses_and_resume_retries_save_without_redownload(self):
        task = self.create(2)
        save = self.service.finish_segment
        blocked = threading.Event()

        def fail(*args, **kwargs):
            if not blocked.is_set():
                raise sqlite3.OperationalError('disk full')
            return save(*args, **kwargs)

        with patch.object(self.service, 'finish_segment', side_effect=fail):
            Engine.StartTask(self.service, task.id, [0, 1])
            self.wait(lambda: Engine.IsPaused())
            self.assertEqual(len(self.server.received), 1)
            self.assertEqual(self.repository.get(task.id).progress.completed_segments, 0)
            self.assertTrue(Engine.IsBusy())
            self.assertIn('disk full', Engine.errors.get_nowait())
            time.sleep(0.2)
            self.assertTrue(Engine.errors.empty())
            blocked.set()
            Engine.Resume()
            self.finish()
        self.assertEqual(len(self.server.received), 2)
        self.assertEqual(self.repository.get(task.id).status, TaskStatus.WAITING_MERGE)
        self.assertFalse(Engine._unsaved)

    def test_deleted_task_is_not_recreated_by_late_result(self):
        task = self.create()
        self.server.mode = 'slow'
        Engine.StartTask(self.service, task.id, [0])
        self.wait(lambda: bool(self.server.received))
        self.repository.delete(task.id)
        self.finish()
        self.assertIsNone(self.repository.get(task.id))
        self.assertFalse(Engine.IsBusy())


if __name__ == '__main__':
    unittest.main()
