from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

from pydantic import ValidationError

from src.schemas.task import (
    TASK_ADAPTER, M3U8Details, M3U8Task, MP4Details, MP4Task,
    RTMPDetails, RTMPTask, SourceType, TaskProgress, TaskSegment, TaskStatus,
)


class TaskModelTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(prefix='avdownloader-model-test-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()

    def m3u8(self, name='playlist'):
        return M3U8Task(name=name, save_dir=self.root, source_type=SourceType.M3U8,
                       source_url='https://example.com/index.m3u8')

    def test_three_types_round_trip_with_stable_ids(self):
        tasks = [
            self.m3u8(),
            M3U8Task(name='pattern', save_dir=self.root, source_type=SourceType.TS_PATTERN,
                     details=M3U8Details(ts_pattern='https://example.com/{index}.ts')),
            MP4Task(name='video', save_dir=self.root, source_url='https://example.com/a.mp4?token=a%2Bb',
                    details=MP4Details(target_path='a.mp4', temporary_path='a.mp4.part')),
            RTMPTask(name='live', save_dir=self.root, source_url='rtmps://example.com/live/key',
                     details=RTMPDetails(target_path='recording.mkv')),
        ]
        for task in tasks:
            with self.subTest(type=task.type, source=task.source_type):
                restored = TASK_ADAPTER.validate_json(task.model_dump_json())
                self.assertEqual(restored, task)
                self.assertEqual(restored.id, task.id)
                self.assertEqual(restored.save_dir, self.root)
        self.assertEqual(len({task.id for task in tasks}), 4)
        self.assertEqual(tasks[0].type, tasks[1].type)
        with self.assertRaises(ValidationError):
            tasks[0].id = uuid4()

    def test_progress_unknown_totals_and_live_have_no_fake_percentage(self):
        task = self.m3u8()
        self.assertIsNone(task.percent)
        task.progress = TaskProgress(total_segments=4, completed_segments=1)
        self.assertEqual(task.percent, 25)
        mp4 = MP4Task(name='mp4', save_dir=self.root, source_url='https://example.com/video',
                      details=MP4Details(target_path='a.mp4', temporary_path='a.part'),
                      progress=TaskProgress(downloaded_bytes=10))
        self.assertIsNone(mp4.percent)
        mp4.progress = TaskProgress(downloaded_bytes=10, total_bytes=20)
        self.assertEqual(mp4.percent, 50)
        live = RTMPTask(name='live', save_dir=self.root, source_url='rtmp://example.com/live',
                        details=RTMPDetails(target_path='live.mkv'),
                        progress=TaskProgress(recorded_seconds=120, downloaded_bytes=1024))
        self.assertIsNone(live.percent)

    def test_rejects_invalid_paths_progress_and_mismatched_types(self):
        for path in ('../a.ts', '/a.ts', 'C:\\outside.ts', '\\\\host\\share\\a.ts', 'a/../../b.ts', '', 'a/'):
            with self.subTest(path=path), self.assertRaises(ValidationError):
                TaskSegment(sequence=0, relative_path=path)
        for data in ({'total_segments': 1, 'completed_segments': 2}, {'downloaded_bytes': -1},
                     {'downloaded_bytes': 11, 'total_bytes': 10}, {'recorded_seconds': float('nan')}):
            with self.subTest(data=data), self.assertRaises(ValidationError):
                TaskProgress(**data)
        with self.assertRaises(ValidationError):
            M3U8Task(name='bad', save_dir=Path('relative'), source_type=SourceType.M3U8,
                     source_url='https://example.com/index.m3u8')
        mp4 = dict(type='mp4', name='bad', save_dir=str(self.root), source_type='ts_pattern',
                   source_url='https://example.com/a.mp4', details=dict(target_path='a.mp4', temporary_path='a.part'))
        with self.assertRaises(ValidationError):
            TASK_ADAPTER.validate_python(mp4)
        with self.assertRaises(ValidationError):
            RTMPTask(name='bad', save_dir=self.root, source_url='https://example.com/live',
                     details=RTMPDetails(target_path='live.mkv'))

    def test_segment_order_is_identity_not_url(self):
        segments = [TaskSegment(sequence=i, relative_path=f'{i}.ts', source_url='https://example.com/same.ts')
                    for i in range(2)]
        self.assertEqual(len(M3U8Details(segments=segments).segments), 2)
        with self.assertRaises(ValidationError):
            M3U8Details(segments=[segments[0], segments[0]])

    def test_per_task_collections_are_independent(self):
        first, second = self.m3u8('one'), self.m3u8('two')
        first.details.segments.append(TaskSegment(sequence=0, relative_path='a.ts'))
        self.assertEqual(second.details.segments, [])

    def test_source_and_status_must_match_task_type(self):
        with self.assertRaises(ValidationError):
            M3U8Task(name='bad', save_dir=self.root, source_type=SourceType.M3U8)
        with self.assertRaises(ValidationError):
            MP4Task(name='bad', save_dir=self.root, source_url='https://example.com/a.mp4',
                    details=MP4Details(target_path='a.mp4', temporary_path='a.part'), status=TaskStatus.MERGING)


if __name__ == '__main__':
    unittest.main()
