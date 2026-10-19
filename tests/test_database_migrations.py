"""使用独立的旧版 SQL 快照，验证真实升级、备份及失败恢复。"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
from pathlib import Path
import shutil
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from src.schemas.task import (M3U8Task, M3U8Details, MP4Task, MP4Details,
                              RTMPTask, RTMPDetails, SourceType, TaskSegment, TaskOutput)
from src.storage import migrations
from src.storage.schema import metadata
from src.storage.task_repository import TaskDataError, TaskRepository


class DatabaseMigrationTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix='avdownloader-migration-')
        self.addCleanup(temporary.cleanup)
        # 覆盖中文、空格、URL 特殊字符；数据库路径不能被当成 URL 拼接。
        self.root = Path(temporary.name) / '数据库 100% #'
        self.root.mkdir()
        self.path = self.root / 'downloads.db'

    def legacy(self):
        tasks = [
            M3U8Task(name='旧分片', save_dir=self.root / 'video', source_type=SourceType.M3U8,
                     source_url='https://example.com/index.m3u8?token=a%2Bb',
                     details=M3U8Details(playlist_path='video/download.m3u8', segments=[
                         TaskSegment(sequence=0, relative_path='video/segments/000000.ts',
                                     source_url='https://example.com/0.ts')]),
                     outputs=[TaskOutput(relative_path='video.mp4', kind='merged')]),
            MP4Task(name='旧 MP4', save_dir=self.root / 'direct', source_url='https://example.com/a.mp4',
                    details=MP4Details(target_path='a.mp4', temporary_path='a.mp4.part')),
            RTMPTask(name='旧直播', save_dir=self.root / 'live', source_url='rtmp://example.com/live',
                     details=RTMPDetails(target_path='live.mkv')),
        ]
        sql = Path(__file__).with_name('fixtures').joinpath('database_v1.sql').read_text(encoding='utf-8')
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.executescript(sql)
            for task in tasks:
                data = task.model_dump(mode='json')
                details = data.pop('details')
                segments = details.pop('segments', [])
                outputs = data.pop('outputs')
                data['progress'] = json.dumps(data['progress'])
                columns = ','.join(data)
                values = ','.join('?' for _ in data)
                connection.execute(f'INSERT INTO tasks({columns}) VALUES ({values})', tuple(data.values()))
                connection.execute(f'INSERT INTO task_{data["type"]} VALUES (?, ?)',
                                   (data['id'], json.dumps(details, ensure_ascii=False)))
                for segment in segments:
                    connection.execute('INSERT INTO task_segments VALUES (?, ?, ?)',
                                       (data['id'], segment['sequence'], json.dumps(segment)))
                for index, output in enumerate(outputs):
                    connection.execute('INSERT INTO task_outputs VALUES (?, ?, ?, ?)',
                                       (data['id'], output['id'], index, json.dumps(output)))
        return tasks

    def snapshot(self, path=None):
        with closing(sqlite3.connect(path or self.path)) as connection:
            names = connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
            return {name: connection.execute(f'SELECT * FROM "{name}" ORDER BY rowid').fetchall()
                    for name, in names if name != 'alembic_version'}

    def version(self):
        with closing(sqlite3.connect(self.path)) as connection:
            return (connection.execute('PRAGMA user_version').fetchone()[0],
                    connection.execute('SELECT version_num FROM alembic_version').fetchone()[0])

    def scripts(self, *, failing=False):
        location = self.root / 'migration scripts'
        shutil.copytree(migrations.SCRIPT_LOCATION, location, ignore=shutil.ignore_patterns('__pycache__'))
        (location / 'versions' / '0003_test.py').write_text('''
from alembic import op
revision = '0003'
down_revision = '0002'
def upgrade():
    op.execute('CREATE TABLE migration_probe (value TEXT NOT NULL)')
    op.execute("INSERT INTO migration_probe VALUES ('upgraded')")
    op.execute("UPDATE tasks SET last_error='migrated'")
''', encoding='utf-8')
        last = "op.execute('SELECT * FROM deliberately_missing_table')" if failing else (
            "op.execute('CREATE INDEX migration_probe_value ON migration_probe(value)')")
        (location / 'versions' / '0004_test.py').write_text(f'''
from alembic import op
revision = '0004'
down_revision = '0003'
def upgrade():
    {last}
''', encoding='utf-8')
        return location

    def test_legacy_tasks_are_preserved_and_backed_up_before_adoption(self):
        tasks = self.legacy()
        original = self.snapshot()
        repo = TaskRepository(self.path)
        self.assertEqual(self.version(), (2, '0002'))
        self.assertEqual(self.snapshot(), original)
        for task in tasks:
            self.assertEqual(repo.get(task.id), task)
            self.assertFalse(task.save_dir.exists())
        backups = list((self.root / 'backups').glob('*.db'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(self.snapshot(backups[0]), original)
        with closing(sqlite3.connect(backups[0])) as connection:
            self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0], 1)
            self.assertIsNone(connection.execute(
                "SELECT name FROM sqlite_master WHERE name='alembic_version'").fetchone())
        repo.mutate(tasks[0].id, lambda task: setattr(task, 'last_error', '仍可写入'))
        self.assertEqual(TaskRepository(self.path).get(tasks[0].id).last_error, '仍可写入')

    def test_new_database_matches_mapping_and_reopen_does_not_write_or_backup(self):
        repo = TaskRepository(self.path)
        self.assertEqual(self.version(), (2, '0002'))
        with repo.engine.connect() as connection:
            differences = compare_metadata(MigrationContext.configure(connection), metadata)
        self.assertEqual(differences, [])
        before = self.path.read_bytes()
        with patch.object(migrations, '_backup', side_effect=AssertionError('unexpected backup')):
            TaskRepository(self.path)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse((self.root / 'backups').exists())

    def test_multiple_pending_revisions_run_in_order_and_only_once(self):
        tasks = self.legacy()
        location = self.scripts()
        with patch.object(migrations, 'SCRIPT_LOCATION', location):
            repo = TaskRepository(self.path)
            self.assertEqual(self.version(), (4, '0004'))
            for task in tasks:
                self.assertEqual(repo.get(task.id).last_error, 'migrated')
            before = self.path.read_bytes()
            TaskRepository(self.path)
            self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(len(list((self.root / 'backups').glob('*.db'))), 1)

    def test_failed_later_revision_rolls_back_ddl_data_and_both_versions(self):
        self.legacy()
        TaskRepository(self.path)
        before = self.snapshot()
        location = self.scripts(failing=True)
        with patch.object(migrations, 'SCRIPT_LOCATION', location):
            with self.assertRaises(sqlite3.OperationalError):
                TaskRepository(self.path)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.version(), (2, '0002'))
        self.assertEqual(len(list((self.root / 'backups').glob('*.db'))), 2)
        self.assertEqual(TaskRepository(self.path).list_tasks()[0].last_error, None)

    def test_failed_first_upgrade_does_not_leave_a_version_table(self):
        self.legacy()
        before = self.path.read_bytes()
        location = self.scripts(failing=True)
        with patch.object(migrations, 'SCRIPT_LOCATION', location):
            with self.assertRaises(sqlite3.OperationalError):
                TaskRepository(self.path)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(TaskRepository(self.path).list_tasks()[0].last_error, None)

    def test_backup_failure_prevents_any_database_change(self):
        self.legacy()
        before = self.path.read_bytes()
        with patch.object(migrations, '_backup', side_effect=OSError('backup disk full')):
            with self.assertRaisesRegex(OSError, 'backup disk full'):
                TaskRepository(self.path)
        self.assertEqual(self.path.read_bytes(), before)

    def test_unversioned_populated_database_is_not_claimed(self):
        self.legacy()
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute('PRAGMA user_version=0')
        before = self.path.read_bytes()
        with self.assertRaisesRegex(TaskDataError, '未标记版本'):
            TaskRepository(self.path)
        self.assertEqual(self.path.read_bytes(), before)

    def test_mismatched_legacy_schema_is_not_claimed(self):
        self.legacy()
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute('ALTER TABLE tasks ADD COLUMN unknown TEXT')
        before = self.path.read_bytes()
        with self.assertRaisesRegex(TaskDataError, '字段不匹配'):
            TaskRepository(self.path)
        self.assertEqual(self.path.read_bytes(), before)

    def test_unknown_revision_is_not_overwritten(self):
        TaskRepository(self.path)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("UPDATE alembic_version SET version_num='2'")
        before = self.path.read_bytes()
        with self.assertRaises(TaskDataError):
            TaskRepository(self.path)
        self.assertEqual(self.path.read_bytes(), before)

    def test_foreign_key_corruption_blocks_upgrade(self):
        self.legacy()
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("INSERT INTO task_mp4 VALUES ('missing-task', '{}')")
        before = self.path.read_bytes()
        with self.assertRaisesRegex(TaskDataError, '失效的关联'):
            TaskRepository(self.path)
        self.assertEqual(self.path.read_bytes(), before)

    def test_parallel_startup_migrates_legacy_database_once(self):
        self.legacy()
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(lambda _: len(TaskRepository(self.path).list_tasks()), range(4)))
        self.assertEqual(results, [3] * 4)
        self.assertEqual(self.version(), (2, '0002'))
        self.assertEqual(len(list((self.root / 'backups').glob('*.db'))), 1)

    def test_wal_database_backup_contains_committed_wal_rows(self):
        tasks = self.legacy()
        with closing(sqlite3.connect(self.path)) as keeper:
            keeper.execute('PRAGMA journal_mode=WAL')
            keeper.execute('PRAGMA wal_autocheckpoint=0')
            keeper.execute("UPDATE tasks SET last_error='in-wal'")
            keeper.commit()
            self.assertGreater(Path(str(self.path) + '-wal').stat().st_size, 0)
            repo = TaskRepository(self.path)
            self.assertEqual(repo.get(tasks[0].id).last_error, 'in-wal')
            backup, = (self.root / 'backups').glob('*.db')
            with closing(sqlite3.connect(backup)) as connection:
                self.assertEqual(connection.execute('SELECT last_error FROM tasks').fetchall(), [('in-wal',)] * 3)

    def test_parallel_initialization_of_independent_databases(self):
        paths = [self.root / f'parallel-{index}.db' for index in range(4)]
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(lambda path: TaskRepository(path).list_tasks(), paths))
        self.assertEqual(results, [[]] * 4)

    def test_python_migration_failure_is_reported_and_can_be_retried(self):
        self.legacy()
        before = self.path.read_bytes()
        location = self.scripts()
        (location / 'versions' / '0004_test.py').write_text('''
revision = '0004'
down_revision = '0003'
def upgrade():
    raise RuntimeError('simulated Python migration failure')
''', encoding='utf-8')
        with patch.object(migrations, 'SCRIPT_LOCATION', location):
            with self.assertRaisesRegex(TaskDataError, 'simulated Python migration failure'):
                TaskRepository(self.path)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(len(TaskRepository(self.path).list_tasks()), 3)


if __name__ == '__main__':
    unittest.main()
