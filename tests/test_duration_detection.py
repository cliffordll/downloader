from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
import subprocess
import unittest
from unittest.mock import patch

from src.media.m3u8.ffmpeg_converter import FFmpegConverter
from src.core.task_service import TaskService
from src.storage.task_repository import TaskRepository
from src.schemas.task import FileStatus, TaskStatus


class DurationDetectionTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.repository = TaskRepository(self.root / 'downloads.db')
        self.service = TaskService(self.repository)

    def create(self, enabled=True):
        task = self.service.create_m3u8(self.root / 'video', 'https://example.com/index.m3u8',
            '#EXTM3U\n#EXTINF:5,\na.ts\n#EXTINF:5,\nb.ts\n', detect_duration=enabled)
        for segment in task.details.segments:
            path = task.save_dir / segment.relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'media')
            task = self.service.finish_segment(task.id, segment.sequence, path, True)
        return task

    def test_persists_actual_duration_and_failure_fallback_without_redetecting(self):
        task = self.create()
        with patch('src.core.task_service.FFmpegConverter.ProbeDuration', side_effect=[6.25, ValueError('bad media')]) as probe:
            record = self.service.detect_durations(task.id)
            self.service.detect_durations(task.id)
            self.assertEqual(probe.call_count, 2)
        first, second = record.details.segments
        self.assertEqual((first.duration, first.duration_status), (6.25, 'detected'))
        self.assertEqual((second.duration, second.duration_status, second.duration_error),
                         (5, 'failed', 'bad media'))
        self.assertEqual(second.status, FileStatus.COMPLETED)
        self.assertEqual(record.status, TaskStatus.WAITING_MERGE)
        self.assertTrue(TaskRepository(self.root / 'downloads.db').get(task.id).details.detect_duration)
        content = self.service.local_playlist(record)
        self.assertIn('#EXT-X-TARGETDURATION:7', content)
        self.assertIn('#EXTINF:6.25,', content)
        self.service.begin_merge(task.id)

    def test_merge_waits_for_detection(self):
        task = self.create()
        with self.assertRaisesRegex(ValueError, '检测'):
            self.service.begin_merge(task.id)

    def test_unchecked_skips_probe_and_allows_merge(self):
        task = self.create(False)
        with patch('src.core.task_service.FFmpegConverter.ProbeDuration') as probe:
            self.service.detect_durations(task.id)
            probe.assert_not_called()
        self.service.begin_merge(task.id)

    def test_exit_keeps_pending_for_restart(self):
        task = self.create()
        stop = Event()
        def finish(_):
            stop.set()
            return 4.2
        with patch('src.core.task_service.FFmpegConverter.ProbeDuration', side_effect=finish):
            self.service.detect_durations(task.id, stop)
        self.assertEqual(self.repository.get(task.id).details.segments[0].duration_status, 'pending')
        with patch('src.core.task_service.FFmpegConverter.ProbeDuration', return_value=4.2):
            TaskService(self.repository).detect_durations(task.id)
        self.assertEqual(self.repository.get(task.id).details.segments[0].duration, 4.2)

    def test_deleted_task_is_not_recreated(self):
        task = self.create()
        def finish(_):
            self.repository.delete(task.id)
            return 4.2
        with patch('src.core.task_service.FFmpegConverter.ProbeDuration', side_effect=finish):
            self.assertIsNone(self.service.detect_durations(task.id))
        self.assertIsNone(self.repository.get(task.id))

    def test_replaced_file_does_not_receive_old_duration(self):
        task = self.create()
        def finish(path):
            path.write_bytes(b'changed media')
            return 4.2
        with patch('src.core.task_service.FFmpegConverter.ProbeDuration', side_effect=finish):
            record = self.service.detect_durations(task.id)
        self.assertTrue(all(s.duration_status == 'pending' for s in record.details.segments))

    def test_ffprobe_parsing_and_invalid_results(self):
        with patch('src.media.m3u8.ffmpeg_converter.SysSetting.GetFFprobe', return_value='ffprobe'), \
                patch('src.media.m3u8.ffmpeg_converter.subprocess.run') as run:
            run.return_value = SimpleNamespace(returncode=0, stdout='{"format":{"duration":"4.25"}}', stderr='')
            self.assertEqual(FFmpegConverter.ProbeDuration(self.root / 'a.ts'), 4.25)
            self.assertEqual(run.call_args.kwargs['timeout'], 15)
            for value in ('NaN', '-1', '0'):
                run.return_value.stdout = '{"format":{"duration":"' + value + '"}}'
                with self.assertRaises(ValueError):
                    FFmpegConverter.ProbeDuration(self.root / 'a.ts')
            run.side_effect = subprocess.TimeoutExpired('ffprobe', 15)
            with self.assertRaises(subprocess.TimeoutExpired):
                FFmpegConverter.ProbeDuration(self.root / 'a.ts')


if __name__ == '__main__':
    unittest.main()
