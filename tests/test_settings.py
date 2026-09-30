import json
import os
from pathlib import Path
from queue import Queue
from tempfile import TemporaryDirectory
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import requests

from src.managers.sys_setting import SysSetting
from src.managers.downloader import Downloader
from src.managers.converter import Converter


class SettingsTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory(prefix='downloader-settings-test-')
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.config = self.root / 'config' / 'settings.json'
        for patcher in (patch.object(SysSetting, 'ConfigPath', return_value=self.config),
                        patch.object(SysSetting, '_values', None),
                        patch.object(SysSetting, '_load_error', '')):
            patcher.start()
            self.addCleanup(patcher.stop)

    def values(self, **changes):
        result = SysSetting.Defaults()
        result['download_dir'] = str(self.root / 'downloads')
        result.update(changes)
        return result

    def test_saved_values_survive_reload(self):
        executable = self.root / 'ffmpeg.exe'
        executable.touch()
        values = self.values(max_workers=2, request_interval=0.7, max_retries=4,
                             connect_timeout=7, read_timeout=45, ffmpeg_path=str(executable), auto_merge=True,
                             default_expand_tasks=True)
        SysSetting.Save(values)
        SysSetting._values = None
        self.assertEqual(SysSetting.GetAll(), values)
        self.assertEqual(SysSetting.GetTimeout(), (7, 45))
        self.assertEqual(SysSetting.GetFFmpeg(), str(executable))
        self.assertEqual(SysSetting.GetWorkPath(), str(self.root / 'downloads') + os.sep)

    def test_invalid_settings_do_not_change_saved_file(self):
        SysSetting.Save(self.values())
        before = self.config.read_bytes()
        for changes in ({'max_workers': 0}, {'max_workers': 1.5}, {'max_workers': True},
                        {'request_interval': float('nan')}, {'request_interval': -1},
                        {'max_retries': 11}, {'connect_timeout': 0}, {'read_timeout': 601},
                        {'download_dir': 'relative'}, {'ffmpeg_path': str(self.root / 'missing.exe')},
                        {'auto_merge': 'true'}, {'default_expand_tasks': 'true'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                SysSetting.Save(self.values(**changes))
            self.assertEqual(self.config.read_bytes(), before)

    def test_failed_atomic_replace_preserves_previous_settings(self):
        SysSetting.Save(self.values())
        before = self.config.read_bytes()
        with patch('src.managers.sys_setting.os.replace', side_effect=OSError('denied')):
            with self.assertRaises(OSError):
                SysSetting.Save(self.values(max_workers=1))
        self.assertEqual(self.config.read_bytes(), before)
        self.assertEqual(SysSetting.GetMaxWorkers(), 3)
        self.assertEqual(list(self.config.parent.glob('*.tmp')), [])

    def test_broken_json_does_not_get_overwritten_on_load(self):
        self.config.parent.mkdir(parents=True)
        self.config.write_text('{broken')
        self.assertEqual(SysSetting.GetAll(), SysSetting.Defaults())
        self.assertTrue(SysSetting._load_error)
        self.assertEqual(self.config.read_text(), '{broken')

    def test_defaults_do_not_follow_current_working_directory(self):
        expected = SysSetting.Defaults()['download_dir']
        with patch('os.getcwd', return_value=str(self.root)):
            self.assertEqual(SysSetting.GetWorkPath(), os.path.join(expected, ''))
        self.assertFalse(self.config.exists())

    def test_ffmpeg_detection_prefers_project_scripts_over_system_path(self):
        local = self.root / 'scripts' / ('ffmpeg.exe' if os.name == 'nt' else 'ffmpeg')
        local.parent.mkdir()
        local.touch()
        module = str(self.root / 'src' / 'managers' / 'sys_setting.py')
        with patch('src.managers.sys_setting.__file__', module), \
             patch('src.managers.sys_setting.shutil.which', return_value='system-ffmpeg') as which:
            self.assertEqual(SysSetting.GetFFmpeg(), str(local))
            which.assert_not_called()
            local.unlink()
            self.assertEqual(SysSetting.GetFFmpeg(), 'system-ffmpeg')

    def test_explicit_ffmpeg_path_overrides_automatic_detection(self):
        custom = self.root / 'custom-ffmpeg.exe'
        custom.touch()
        SysSetting.Save(self.values(ffmpeg_path=str(custom)))
        with patch('src.managers.sys_setting.shutil.which') as which:
            self.assertEqual(SysSetting.GetFFmpeg(), str(custom))
            which.assert_not_called()


class DownloadSettingsTests(unittest.TestCase):
    def setUp(self):
        self.values = SysSetting.Defaults()
        self.values.update(max_workers=2, request_interval=0, max_retries=2)
        for patcher in (
            patch.object(SysSetting, 'GetAll', side_effect=lambda: dict(self.values)),
            patch.object(Downloader, '_shutdown', threading.Event()),
            patch.object(Downloader, '_user_paused', threading.Event()),
            patch.object(Downloader, '_next_request', 0.0),
            patch.object(Downloader, '_paused_until', 0.0),
            patch.object(Downloader, '_pending', set()),
            patch.object(Downloader, '_requesting', set()),
            patch.object(Downloader, '_failed', set()),
            patch.object(Downloader, '_errors', {}),
            patch.object(Downloader, '_paused_files', set()),
            patch.object(Downloader, 'threadQueue', Queue()),
            patch.object(Downloader, 'isStop', True),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def response(self, status=200, headers=None):
        return SimpleNamespace(status_code=status, content=b'content', headers=headers or {}, close=Mock())

    def test_pause_keeps_queue_and_resume_downloads_remaining_files(self):
        self.values['max_workers'] = 1
        started, release, delivered, next_started = [threading.Event() for _ in range(4)]

        def download(uri, filename):
            if uri == 'first':
                started.set()
                release.wait(3)
            else:
                next_started.set()
            return True, filename

        def callback(success, filename, item):
            if filename == 'first.ts':
                delivered.set()

        with patch.object(Downloader, '_DownLoadFile', side_effect=download), \
             patch('wx.CallAfter', side_effect=lambda func, *args: func(*args)):
            Downloader.DownloadTSFile('first', 'first.ts', callback, None)
            try:
                self.assertTrue(started.wait(2))
                Downloader.Pause()
                Downloader.DownloadTSFile('second', 'second.ts', callback, None)
                release.set()
                self.assertTrue(delivered.wait(2))
                self.assertFalse(next_started.wait(0.2))
                self.assertTrue(Downloader.IsBusy())
                Downloader.Resume()
                self.assertTrue(next_started.wait(2))
            finally:
                release.set()
                Downloader.Resume()
                Downloader._master.join(4)
            self.assertFalse(Downloader._master.is_alive())
            self.assertFalse(Downloader.IsBusy())

    def test_pause_blocks_segment_requests_but_not_playlist_loading(self):
        entered = threading.Event()
        Downloader.Pause()
        with patch('requests.get', return_value=self.response()) as get:
            def download():
                entered.set()
                return Downloader.DownloadContent('segment', respect_pause=True)
            worker = threading.Thread(target=download)
            worker.start()
            try:
                self.assertTrue(entered.wait(2))
                worker.join(0.2)
                self.assertTrue(worker.is_alive())
                get.assert_not_called()
                self.assertTrue(Downloader.DownloadContent('playlist')[0])
                get.assert_called_once_with('playlist', timeout=SysSetting.GetTimeout())
                Downloader.Resume()
                worker.join(2)
                self.assertFalse(worker.is_alive())
                self.assertEqual(get.call_count, 2)
            finally:
                Downloader._shutdown.set()
                worker.join(2)

    def test_shutdown_exits_paused_scheduler_without_starting_queue(self):
        Downloader.Pause()
        with patch.object(Downloader, '_DownLoadFile') as download:
            Downloader.DownloadTSFile('queued', 'queued.ts', Mock(), None)
            Downloader.Shutdown()
            Downloader._master.join(2)
            self.assertFalse(Downloader._master.is_alive())
            download.assert_not_called()
            self.assertFalse(Downloader.IsBusy())

    def test_pause_snapshot_tracks_only_requests_still_in_flight(self):
        started, release = threading.Event(), threading.Event()
        key = Downloader.FileKey('inflight.ts')

        def request(*args, **kwargs):
            started.set()
            release.wait(3)
            return self.response()

        with patch('requests.get', side_effect=request):
            worker = threading.Thread(target=Downloader.DownloadContent,
                                      args=('url',), kwargs=dict(respect_pause=True, request_key=key))
            worker.start()
            try:
                self.assertTrue(started.wait(2))
                Downloader.Pause()
                self.assertEqual(Downloader.Snapshot()['requesting'], {key})
                release.set()
                worker.join(2)
                self.assertFalse(worker.is_alive())
                self.assertEqual(Downloader.Snapshot()['requesting'], set())
                self.assertTrue(Downloader.IsPaused())
            finally:
                release.set()
                worker.join(4)

    def test_request_receives_both_configured_timeouts(self):
        self.values.update(connect_timeout=4, read_timeout=23)
        response = self.response()
        with patch('requests.get', return_value=response) as get:
            self.assertEqual(Downloader.DownloadContent('https://example.com'), (True, b'content'))
        self.assertEqual(get.call_args.kwargs['timeout'], (4, 23))
        response.close.assert_called_once()

    def test_resume_one_task_keeps_other_task_paused(self):
        self.values['max_workers'] = 1
        first_started, second_started = threading.Event(), threading.Event()
        Downloader.Pause()

        def download(uri, filename):
            (first_started if uri == 'first' else second_started).set()
            return True, filename

        with patch.object(Downloader, '_DownLoadFile', side_effect=download), \
             patch('wx.CallAfter', side_effect=lambda func, *args: func(*args)):
            Downloader.DownloadTSFile('first', 'first.ts', Mock(), None)
            Downloader.DownloadTSFile('second', 'second.ts', Mock(), None)
            try:
                Downloader.ResumeFiles({Downloader.FileKey('second.ts')})
                self.assertTrue(second_started.wait(2))
                self.assertFalse(first_started.wait(0.2))
                self.assertNotIn(Downloader.FileKey('first.ts'), Downloader.Snapshot()['failed'])
                Downloader.ResumeFiles({Downloader.FileKey('first.ts')})
                self.assertTrue(first_started.wait(2))
            finally:
                Downloader.Resume()
                Downloader._master.join(3)
            self.assertFalse(Downloader.IsBusy())

    def test_paused_dispatched_file_returns_to_queue_without_failure(self):
        first_waiting, release, other_started = [threading.Event() for _ in range(3)]
        self.values['max_workers'] = 1
        attempts = []

        def download(uri, filename):
            attempts.append(uri)
            if uri == 'first' and attempts.count('first') == 1:
                first_waiting.set()
                release.wait(3)
                Downloader._WaitForRequest(True, Downloader.FileKey(filename))
                self.fail('paused request should have been requeued')
            if uri == 'other':
                other_started.set()
            return True, filename

        with patch.object(Downloader, '_DownLoadFile', side_effect=download), \
             patch('wx.CallAfter', side_effect=lambda func, *args: func(*args)):
            Downloader.DownloadTSFile('first', 'first.ts', Mock(), None)
            try:
                self.assertTrue(first_waiting.wait(2))
                Downloader.PauseFiles({Downloader.FileKey('first.ts')})
                Downloader.DownloadTSFile('other', 'other.ts', Mock(), None)
                release.set()
                self.assertTrue(other_started.wait(2))
                self.assertFalse(Downloader.Snapshot()['failed'])
                Downloader.ResumeFiles({Downloader.FileKey('first.ts')})
            finally:
                release.set()
                Downloader.Resume()
                Downloader._master.join(3)
            self.assertEqual(attempts, ['first', 'other', 'first'])
            self.assertFalse(Downloader.IsBusy())

    def test_403_is_not_retried(self):
        with patch('requests.get', return_value=self.response(403)) as get:
            success, message = Downloader.DownloadContent('https://example.com')
        self.assertFalse(success)
        self.assertIn('403', message)
        self.assertEqual(get.call_count, 1)

    def test_network_errors_respect_retry_limit(self):
        with patch('requests.get', side_effect=requests.ConnectionError('offline')) as get, \
             patch.object(Downloader._shutdown, 'wait', return_value=False) as backoff:
            self.assertFalse(Downloader.DownloadContent('https://example.com')[0])
        self.assertEqual(get.call_count, 3)
        self.assertEqual([call.args[0] for call in backoff.call_args_list], [1, 2])

    def test_429_blocks_new_requests_until_retry_after_even_when_retries_exhausted(self):
        self.values['max_retries'] = 0
        with patch('requests.get', return_value=self.response(429, {'Retry-After': '30'})):
            now = time.monotonic()
            self.assertFalse(Downloader.DownloadContent('https://example.com')[0])
        self.assertGreaterEqual(Downloader._paused_until, now + 30)
        self.assertGreater(Downloader._RetryAfter('Wed, 01 Jan 2031 00:00:00 GMT', 1), 0)
        self.assertEqual(Downloader._RetryAfter('bad header', 3), 3)

    def test_request_starts_are_spaced(self):
        self.values['request_interval'] = 0.03
        self.assertTrue(Downloader._WaitForRequest())
        started = time.monotonic()
        self.assertTrue(Downloader._WaitForRequest())
        self.assertGreaterEqual(time.monotonic() - started, 0.025)

    def test_scheduler_refills_free_slot_without_waiting_for_slow_task(self):
        slow_started = threading.Event()
        release = threading.Event()
        third_started = threading.Event()
        active = 0
        peak = 0
        lock = threading.Lock()
        results = []

        def download(uri, filename):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            try:
                if uri == 'slow':
                    slow_started.set()
                    release.wait(3)
                elif uri == 'fast':
                    slow_started.wait(3)
                elif uri == 'third':
                    third_started.set()
                return True, filename
            finally:
                with lock:
                    active -= 1

        with patch.object(Downloader, '_DownLoadFile', side_effect=download), \
             patch('wx.CallAfter', side_effect=lambda func, *args: func(*args)):
            for name in ('slow', 'fast', 'third'):
                Downloader.DownloadTSFile(name, name + '.ts', lambda *args: results.append(args), None)
            try:
                self.assertTrue(third_started.wait(2), 'free worker did not start the next file')
                self.assertLessEqual(peak, 2)
            finally:
                release.set()
                Downloader._master.join(4)
            self.assertEqual(len(results), 3)
            self.assertFalse(Downloader.IsBusy())

    def test_successful_file_write_is_atomic(self):
        with TemporaryDirectory() as directory, patch.object(Downloader, 'DownloadContent', return_value=(True, b'data')):
            filename = str(Path(directory) / 'a.ts')
            self.assertTrue(Downloader._DownLoadFile('url', filename)[0])
            self.assertEqual(Path(filename).read_bytes(), b'data')
            self.assertFalse(Path(filename + '.part').exists())

    def test_lower_concurrency_limit_waits_for_existing_requests(self):
        started = threading.Event()
        release = threading.Event()
        fast_done = threading.Event()
        third = threading.Event()

        def download(uri, filename):
            if uri == 'slow':
                started.set()
                release.wait(3)
            elif uri == 'fast':
                started.wait(3)
                self.values['max_workers'] = 1
            else:
                third.set()
            return True, filename

        def callback(success, filename, item):
            if filename == 'fast.ts':
                fast_done.set()

        with patch.object(Downloader, '_DownLoadFile', side_effect=download), \
             patch('wx.CallAfter', side_effect=lambda func, *args: func(*args)):
            for name in ('slow', 'fast', 'third'):
                Downloader.DownloadTSFile(name, name + '.ts', callback, None)
            try:
                self.assertTrue(fast_done.wait(2))
                self.assertFalse(third.wait(0.05))
            finally:
                release.set()
                Downloader._master.join(4)
            self.assertTrue(third.is_set())

    def test_auto_merge_runs_only_when_enabled(self):
        from src.views.main_frame import MainFrame
        event = Mock()
        event.GetData.return_value = {'fileName': 'download.seed'}
        task = SimpleNamespace(parent=SimpleNamespace(fileName='download.seed'), outputs=[])
        model = Mock(fileTree=SimpleNamespace(items=[task]))
        frame = SimpleNamespace(model=model, _CreateM3U8File=Mock(), _CreateMP4File=Mock(),
                                _RefreshWithState=Mock())
        self.values['auto_merge'] = False
        MainFrame.OnAllTSDownload(frame, event)
        frame._CreateMP4File.assert_not_called()
        self.values['auto_merge'] = True
        MainFrame.OnAllTSDownload(frame, event)
        frame._CreateMP4File.assert_called_once()

    def test_custom_ffmpeg_path_and_completion_callback(self):
        process = Mock(stdout=iter([]))
        process.wait.return_value = 0
        with patch.object(SysSetting, 'GetFFmpeg', return_value='custom-ffmpeg'), \
             patch('subprocess.Popen', return_value=process) as popen, \
             patch('os.path.isfile', return_value=True), patch('os.replace') as replace, patch('wx.CallAfter') as deliver:
            callback = Mock()
            Converter._ConvertTSFile('playlist.txt', 'out.mp4', callback)
        self.assertEqual(popen.call_args.args[0][0], 'custom-ffmpeg')
        self.assertEqual(deliver.call_args.args[1:3], (True, 'out.mp4'))
        replace.assert_called_once_with('out.mp4.part.mp4', 'out.mp4')
        callback.assert_not_called()  # queued for the UI thread


if __name__ == '__main__':
    unittest.main()
