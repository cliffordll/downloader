from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch
from uuid import uuid4

from pydantic import ValidationError

from src.config.app_paths import data_dir, database_path
from src.config.sys_setting import SysSetting
from src.storage.task_repository import TaskConflictError, TaskDataError, TaskRepository
from src.schemas.task import (
    M3U8Details, M3U8Task, MP4Details, MP4Task, RTMPDetails, RTMPTask,
    SourceType, TaskOutput, TaskProgress, TaskSegment, TaskStatus,
)


class TaskRepositoryTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(prefix='avdownloader-db-test-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.path = self.root / 'data' / 'downloads.db'
        self.repo = TaskRepository(self.path)

    def task(self, name='任务'):
        return M3U8Task(
            name=name, source_type=SourceType.M3U8,
            source_url='https://example.com/index.m3u8?token=a%2Bb', save_dir=self.root / name,
            details=M3U8Details(playlist_path='index.m3u8', segments=[
                TaskSegment(sequence=i, relative_path=f'{i}.ts', source_url='https://example.com/same.ts')
                for i in range(3)]), progress=TaskProgress(total_segments=3),
            outputs=[TaskOutput(relative_path='成品.mp4', kind='merged')])

    def test_all_sources_survive_reopen_with_independent_directories(self):
        tasks = [self.task(),
                 M3U8Task(name='TS', save_dir=self.root / 'ts', source_type=SourceType.TS_PATTERN,
                          details=M3U8Details(ts_pattern='https://example.com/{index}.ts')),
                 MP4Task(name='MP4', save_dir=self.root / 'mp4', source_url='https://example.com/a.mp4',
                         details=MP4Details(target_path='a.mp4', temporary_path='a.part', supports_ranges=True)),
                 RTMPTask(name='RTMP', save_dir=self.root / 'live', source_url='rtmp://example.com/live',
                          details=RTMPDetails(target_path='live.mkv'))]
        for task in tasks:
            self.repo.create(task)
        reopened = TaskRepository(self.path)
        self.assertEqual(reopened.list_tasks(), sorted(tasks, key=lambda task: (task.created_at, str(task.id))))
        for task in tasks:
            self.assertEqual(reopened.get(task.id), task)
            self.assertFalse(task.save_dir.exists(), '存储任务不应创建下载文件夹')
        self.assertIsNone(reopened.get(uuid4()))

    def test_update_replaces_children_and_does_not_mutate_input(self):
        original = self.repo.create(self.task())
        changed = self.repo.get(original.id)
        changed.name = '新名称'
        changed.status = TaskStatus.PAUSED
        changed.progress = TaskProgress(total_segments=2, completed_segments=1)
        changed.details.segments.pop()
        changed.outputs = [TaskOutput(relative_path='new.mp4', kind='merged')]
        saved = self.repo.update(changed)
        self.assertGreater(saved.updated_at, original.updated_at)
        self.assertEqual(changed.updated_at, original.updated_at)
        self.assertEqual(self.repo.get(original.id), saved)
        saved.last_error = '测试错误'
        self.assertEqual(self.repo.update(saved).last_error, '测试错误')
        with self.assertRaises(TaskConflictError):
            self.repo.update(original)

    def test_failed_child_write_rolls_back_entire_update(self):
        original = self.repo.create(self.task())
        changed = original.model_copy(deep=True)
        changed.name = '不应保留'
        changed.details.segments.pop()
        with sqlite3.connect(self.path) as connection:
            connection.execute("""CREATE TRIGGER fail_output BEFORE INSERT ON task_outputs
                                  BEGIN SELECT RAISE(ABORT, 'simulated failure'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            self.repo.update(changed)
        self.assertEqual(self.repo.get(original.id), original)
        with self.assertRaises(sqlite3.IntegrityError):
            self.repo.create(self.task('写入失败'))
        self.assertEqual(self.repo.list_tasks(), [original])

    def test_delete_cascades_records_but_preserves_files(self):
        task = self.repo.create(self.task())
        task.save_dir.mkdir()
        output = task.save_dir / '成品.mp4'
        output.write_bytes(b'keep')
        other = self.repo.create(self.task('其他任务'))
        self.assertTrue(self.repo.delete(task.id))
        self.assertFalse(self.repo.delete(task.id))
        self.assertEqual(output.read_bytes(), b'keep')
        with sqlite3.connect(self.path) as connection:
            for table in ('task_m3u8', 'task_segments', 'task_outputs'):
                self.assertEqual(connection.execute(f'SELECT COUNT(*) FROM {table} WHERE task_id=?',
                                                    (str(task.id),)).fetchone()[0], 0)
        self.assertEqual(self.repo.list_tasks(), [other])
        with self.assertRaises(TaskConflictError):
            self.repo.update(task)

    def test_duplicate_id_and_mutated_invalid_models_do_not_overwrite(self):
        task = self.repo.create(self.task())
        with self.assertRaises(sqlite3.IntegrityError):
            self.repo.create(task)
        invalid = task.model_copy(deep=True)
        invalid.details.segments.append(invalid.details.segments[0])
        with self.assertRaises(ValidationError):
            self.repo.update(invalid)
        invalid = task.model_copy(deep=True)
        invalid.outputs.append(invalid.outputs[0])
        with self.assertRaises(ValueError):
            self.repo.update(invalid)
        self.assertEqual(self.repo.get(task.id), task)

    def test_concurrent_writers_cannot_silently_overwrite_each_other(self):
        task = self.repo.create(self.task())
        barrier = threading.Barrier(2)

        def write(name):
            repo = TaskRepository(self.path)
            changed = repo.get(task.id)
            changed.name = name
            barrier.wait(timeout=5)
            try:
                return repo.update(changed).name
            except TaskConflictError:
                return 'conflict'

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(write, ('one', 'two')))
        self.assertEqual(results.count('conflict'), 1)
        self.assertIn(self.repo.get(task.id).name, [item for item in results if item != 'conflict'])

    def test_unsupported_database_version_is_not_reset(self):
        task = self.repo.create(self.task())
        with sqlite3.connect(self.path) as connection:
            connection.execute('PRAGMA user_version=99')
        before = self.path.read_bytes()
        with self.assertRaises(TaskDataError):
            TaskRepository(self.path)
        self.assertEqual(self.path.read_bytes(), before)
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(connection.execute('SELECT id FROM tasks').fetchone()[0], str(task.id))

    def test_invalid_stored_data_is_reported_without_overwrite(self):
        task = self.repo.create(self.task())
        with sqlite3.connect(self.path) as connection:
            connection.execute("UPDATE tasks SET progress='{broken' WHERE id=?", (str(task.id),))
        before = self.path.read_bytes()
        with self.assertRaisesRegex(TaskDataError, str(task.id)):
            self.repo.list_tasks()
        self.assertEqual(self.path.read_bytes(), before)

    def test_default_paths_use_new_app_directory_without_legacy_migration(self):
        legacy = self.root / 'M3U8Downloader' / 'settings.json'
        legacy.parent.mkdir()
        legacy.write_text('{old config}', encoding='utf-8')
        with patch('src.config.app_paths.Path.home', return_value=self.root):
            self.assertEqual(data_dir(), self.root / '.avdownloader')
            self.assertEqual(SysSetting.ConfigPath(), data_dir() / 'settings.json')
            self.assertEqual(database_path(), data_dir() / 'downloads.db')
            repo = TaskRepository()
            self.assertEqual(repo.list_tasks(), [])
            self.assertFalse(SysSetting.ConfigPath().exists())
        self.assertEqual(legacy.read_text(encoding='utf-8'), '{old config}')


if __name__ == '__main__':
    unittest.main()
