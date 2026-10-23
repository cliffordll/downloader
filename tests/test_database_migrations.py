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
from uuid import uuid4

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from src.schemas.task import (M3U8Task, M3U8Details, MP4Task, MP4Details,
                              RTMPTask, RTMPDetails, SourceType, TaskSegment, TaskOutput, TaskProgress)
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

    def legacy(self, *, managed=False):
        tasks = [
            M3U8Task(name='旧分片', save_dir=self.root / 'video', source_type=SourceType.M3U8,
                     source_url='https://example.com/index.m3u8?token=a%2Bb',
                     details=M3U8Details(playlist_path='video/download.m3u8', segments=[
                         TaskSegment(sequence=0, segment_path='video/segments/000000.ts',
                                     source_url='https://example.com/0.ts')]),
                     outputs=[TaskOutput(output_path='video.mp4', kind='merged')]),
            MP4Task(name='旧 MP4', save_dir=self.root / 'direct', source_url='https://example.com/a.mp4',
                    details=MP4Details(temporary_path='a.mp4.part'),
                    outputs=[TaskOutput(output_path='a.mp4', kind='download')]),
            RTMPTask(name='旧直播', save_dir=self.root / 'live', source_url='rtmp://example.com/live',
                     details=RTMPDetails(), outputs=[TaskOutput(output_path='live.mkv', kind='recording')]),
        ]
        sql = Path(__file__).with_name('fixtures').joinpath('database_v1.sql').read_text(encoding='utf-8')
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.executescript(sql)
            for task in tasks:
                data = task.model_dump(mode='json')
                data.pop('encrypted', None)  # The v1 fixture predates encrypted-task metadata.
                details = data.pop('details')
                segments = details.pop('segments', [])
                details.pop('detecting', None)
                outputs = data.pop('outputs')
                for segment in segments:
                    segment['relative_path'] = segment.pop('segment_path')
                for output in outputs:
                    output['relative_path'] = output.pop('output_path')
                # The v1 fixture stores the type details as JSON, before the
                # later migrations moved these legacy output paths to columns.
                if data['type'] == 'mp4':
                    details['target_path'] = outputs[0]['relative_path']
                elif data['type'] == 'rtmp':
                    details['target_path'] = outputs[0]['relative_path']
                data['progress'] = json.dumps(data['progress'])
                columns = ','.join(data)
                values = ','.join('?' for _ in data)
                connection.execute(f'INSERT INTO tasks({columns}) VALUES ({values})', tuple(data.values()))
                connection.execute(f'INSERT INTO task_{data["type"]} VALUES (?, ?)',
                                   (data['id'], json.dumps(details, ensure_ascii=False)))
                for segment in segments:
                    # Historical v1 rows predate the key-entry schema.
                    segment.pop('entry_type', None)
                    segment.pop('encryption', None)
                    if segment.get('status') in ('waiting_key', 'decrypting'):
                        segment['status'] = 'pending'
                    connection.execute('INSERT INTO task_segments VALUES (?, ?, ?)',
                                       (data['id'], segment['sequence'], json.dumps(segment)))
                for index, output in enumerate(outputs):
                    connection.execute('INSERT INTO task_outputs VALUES (?, ?, ?, ?)',
                                       (data['id'], output['id'], index, json.dumps(output)))
            if managed:
                connection.execute('CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)')
                connection.execute("INSERT INTO alembic_version VALUES ('0002')")
                connection.execute('PRAGMA user_version=2')
        return tasks

    def snapshot(self, path=None):
        with closing(sqlite3.connect(path or self.path)) as connection:
            connection.row_factory = sqlite3.Row
            names = connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
            result = {}
            for name, in names:
                if name == 'alembic_version':
                    continue
                rows = [dict(row) for row in connection.execute(f'SELECT * FROM "{name}" ORDER BY rowid')]
                if name == 'tasks':
                    for row in rows:
                        if 'progress' in row:
                            progress = json.loads(row['progress'])
                        else:
                            progress = {field: row.pop(field) for field in TaskProgress.model_fields}
                        row['progress'] = TaskProgress.model_validate(progress).model_dump(mode='json')
                if name in ('task_m3u8', 'task_mp4', 'task_rtmp'):
                    model = {'task_m3u8': M3U8Details, 'task_mp4': MP4Details, 'task_rtmp': RTMPDetails}[name]
                    for row in rows:
                        if 'data' in row:
                            details = json.loads(row.pop('data'))
                            details.pop('target_path', None)
                        else:
                            details = {key: value for key, value in row.items() if key != 'task_id'}
                            # target_path is legacy duplication now represented
                            # by task_outputs and is intentionally omitted.
                            details.pop('target_path', None)
                            for key in ('work_dir_missing', 'file_missing'):
                                if details.get(key, False) is None:
                                    details.pop(key)
                            if 'request_headers' in details:
                                details['request_headers'] = json.loads(details['request_headers'])
                        normalized = model.model_validate(details).model_dump(mode='json')
                        task_id = row['task_id']
                        row.clear()
                        row.update(task_id=task_id, data=normalized)
                if name in ('task_segments', 'task_outputs'):
                    model = TaskSegment if name == 'task_segments' else TaskOutput
                    normalized = []
                    for row in rows:
                        if 'data' in row:
                            data = json.loads(row.pop('data'))
                            old_name, new_name = (('relative_path', 'segment_path') if name == 'task_segments'
                                                  else ('relative_path', 'output_path'))
                            if old_name in data and new_name not in data:
                                data[new_name] = data.pop(old_name)
                            data = model.model_validate(data).model_dump(mode='json')
                            row = {**data, **row}
                        normalized.append(row)
                    rows = normalized
                result[name] = rows
            return result

    def version(self):
        with closing(sqlite3.connect(self.path)) as connection:
            return (connection.execute('PRAGMA user_version').fetchone()[0],
                    connection.execute('SELECT version_num FROM alembic_version').fetchone()[0])

    def scripts(self, *, failing=False):
        location = self.root / 'migration scripts'
        shutil.copytree(migrations.SCRIPT_LOCATION, location, ignore=shutil.ignore_patterns('__pycache__'))
        (location / 'versions' / '0009_test.py').write_text('''
from alembic import op
revision = '0009'
down_revision = '0008'
def upgrade():
    op.execute('CREATE TABLE migration_probe (value TEXT NOT NULL)')
    op.execute("INSERT INTO migration_probe VALUES ('upgraded')")
    op.execute("UPDATE tasks SET last_error='migrated'")
''', encoding='utf-8')
        last = "op.execute('SELECT * FROM deliberately_missing_table')" if failing else (
            "op.execute('CREATE INDEX migration_probe_value ON migration_probe(value)')")
        (location / 'versions' / '0010_test.py').write_text(f'''
from alembic import op
revision = '0010'
down_revision = '0009'
def upgrade():
    {last}
''', encoding='utf-8')
        return location

    def test_legacy_tasks_are_preserved_and_backed_up_before_adoption(self):
        tasks = self.legacy()
        original = self.snapshot()
        repo = TaskRepository(self.path)
        self.assertEqual(self.version(), (8, '0008'))
        for task in tasks:
            migrated = repo.get(task.id)
            self.assertEqual(migrated.type, task.type)
            self.assertEqual(migrated.name, {'m3u8': 'video', 'mp4': 'a', 'rtmp': 'live'}[task.type.value])
            self.assertFalse(task.save_dir.exists())
        migrated_hls = repo.get(tasks[0].id)
        self.assertFalse(migrated_hls.encrypted)
        self.assertEqual(migrated_hls.details.playlist_path, 'download.m3u8')
        self.assertEqual(migrated_hls.details.segments[0].segment_path, 'segments/000000.ts')
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
        self.assertEqual(self.version(), (8, '0008'))
        with repo.engine.connect() as connection:
            differences = compare_metadata(MigrationContext.configure(connection), metadata)
            self.assertIsNone(connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='task_encryption'").first())
            self.assertEqual(connection.exec_driver_sql(
                'SELECT COUNT(*) FROM tasks WHERE encrypted != 0').scalar_one(), 0)
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
            self.assertEqual(self.version(), (10, '0010'))
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
        self.assertEqual(self.version(), (8, '0008'))
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
            connection.execute("UPDATE alembic_version SET version_num='9999'")
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
        self.assertEqual(self.version(), (8, '0008'))
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
        (location / 'versions' / '0009_test.py').write_text('''
revision = '0009'
down_revision = '0008'
def upgrade():
    raise RuntimeError('simulated Python migration failure')
''', encoding='utf-8')
        with patch.object(migrations, 'SCRIPT_LOCATION', location):
            with self.assertRaisesRegex(TaskDataError, 'simulated Python migration failure'):
                TaskRepository(self.path)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(len(TaskRepository(self.path).list_tasks()), 3)

    def test_v2_columns_preserve_all_fields_batches_and_output_order(self):
        tasks = self.legacy(managed=True)
        expected = tasks[0]
        expected.details.segments = [TaskSegment(
            sequence=index, segment_path=f'视频/segments/{index:06d}.ts',
            source_url=f'https://example.com/{index}.ts?token=a%2Bb' if index % 2 else None,
            duration=None if index % 2 else 0.0 if index == 0 else 3.25,
            duration_status=('pending', 'detected', 'failed')[index % 3],
            duration_error='检测失败：中文错误' if index % 3 == 2 else None,
            status=('pending', 'completed', 'failed', 'missing')[index % 4],
            size_bytes=None if index % 2 else index * 100,
            last_error='网络错误' if index % 4 == 2 else None,
        ) for index in range(503)]
        expected.outputs = [TaskOutput(
            output_path=f'输出/{index}.mp4', kind=('merged', 'download', 'recording')[index],
            status=('completed', 'pending', 'failed')[index], size_bytes=(0, None, 1234)[index],
        ) for index in range(3)]
        expected.progress.total_segments = len(expected.details.segments)
        data = expected.model_dump(mode='json')
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute('DELETE FROM task_segments WHERE task_id=?', (str(expected.id),))
            connection.executemany('INSERT INTO task_segments VALUES (?, ?, ?)', [
                (str(expected.id), segment['sequence'], json.dumps(
                    {('relative_path' if key == 'segment_path' else key): value
                     for key, value in segment.items() if key not in ('entry_type', 'encryption')},
                    ensure_ascii=False))
                for segment in data['details']['segments']])
            connection.execute('DELETE FROM task_outputs WHERE task_id=?', (str(expected.id),))
            connection.executemany('INSERT INTO task_outputs VALUES (?, ?, ?, ?)', [
                (str(expected.id), output['id'], position, json.dumps(
                    {('relative_path' if key == 'output_path' else key): value for key, value in output.items()},
                    ensure_ascii=False))
                for position, output in enumerate(data['outputs'])])
            connection.execute('UPDATE tasks SET progress=? WHERE id=?',
                               (json.dumps(data['progress']), str(expected.id)))
        before = self.snapshot()
        repo = TaskRepository(self.path)
        self.assertEqual(self.version(), (8, '0008'))
        expected.name = 'video'
        expected.details.playlist_path = 'download.m3u8'
        self.assertEqual(repo.get(expected.id), expected)
        self.assertNotEqual(self.snapshot(), before)
        backup, = (self.root / 'backups').glob('*.db')
        with closing(sqlite3.connect(backup)) as connection:
            self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0], 2)
            self.assertIn('data', [row[1] for row in connection.execute('PRAGMA table_info(task_segments)')])
        with closing(sqlite3.connect(self.path)) as connection:
            for table in ('task_segments', 'task_outputs'):
                self.assertNotIn('data', [row[1] for row in connection.execute(f'PRAGMA table_info({table})')])
            self.assertEqual(connection.execute(
                'SELECT segment_path, size_bytes, duration FROM task_segments WHERE sequence=0').fetchone(),
                ('视频/segments/000000.ts', 0, 0.0))
        expected.outputs.reverse()
        saved = repo.update(expected)
        self.assertEqual(repo.get(expected.id), saved)
        def change(task):
            task.details.segments[0].duration = 6.5
            task.details.segments[0].duration_status = 'detected'
            task.details.segments.pop()
            task.outputs[0].size_bytes = 4321
        saved = repo.mutate(expected.id, change)
        self.assertEqual(TaskRepository(self.path).get(expected.id), saved)
        self.assertEqual(len(saved.details.segments), 502)
        self.assertEqual(saved.details.segments[0].duration, 6.5)
        self.assertEqual(saved.outputs[0].size_bytes, 4321)
        self.assertTrue(repo.delete(expected.id))
        with closing(sqlite3.connect(self.path)) as connection:
            for table in ('task_segments', 'task_outputs'):
                self.assertEqual(connection.execute(
                    f'SELECT COUNT(*) FROM {table} WHERE task_id=?', (str(expected.id),)).fetchone()[0], 0)
        self.assertIsNotNone(repo.get(tasks[1].id))

    def test_missing_optional_json_fields_use_original_defaults(self):
        tasks = self.legacy(managed=True)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute('UPDATE task_segments SET data=?', (json.dumps({
                'sequence': 0, 'relative_path': 'video/segments/000000.ts',
                'source_url': 'https://example.com/0.ts'}),))
            connection.execute('UPDATE task_outputs SET data=? WHERE task_id=?', (json.dumps({
                'id': str(tasks[0].outputs[0].id), 'relative_path': 'video.mp4', 'kind': 'merged'}),
                str(tasks[0].id)))
        repo = TaskRepository(self.path)
        migrated = repo.get(tasks[0].id)
        self.assertEqual(migrated.name, 'video')
        self.assertEqual(migrated.details.playlist_path, 'download.m3u8')
        self.assertEqual(migrated.details.segments[0].segment_path, 'segments/000000.ts')

    def test_invalid_child_json_rolls_back_and_preserves_v2_original(self):
        cases = [
            ('task_segments', None),  # JSON 损坏。
            ('task_segments', {'sequence': 99}),
            ('task_segments', {'future_field': 'cannot silently discard'}),
            ('task_segments', {'size_bytes': -1}),
            ('task_segments', {'duration_status': 'unknown'}),
            ('task_outputs', {'id': str(uuid4())}),
            ('task_outputs', {'kind': 'unknown'}),
            ('task_outputs', {'relative_path': '../outside.mp4'}),
        ]
        for index, (table, changes) in enumerate(cases):
            with self.subTest(table=table, changes=changes):
                self.path = self.root / f'invalid-{index}.db'
                self.legacy(managed=True)
                with closing(sqlite3.connect(self.path)) as connection, connection:
                    data = json.loads(connection.execute(f'SELECT data FROM {table}').fetchone()[0])
                    raw = '{broken' if changes is None else json.dumps({**data, **changes})
                    connection.execute(f'UPDATE {table} SET data=?', (raw,))
                before = self.path.read_bytes()
                with self.assertRaisesRegex(TaskDataError, '数据异常'):
                    TaskRepository(self.path)
                self.assertEqual(self.path.read_bytes(), before)
                self.assertEqual(self.version(), (2, '0002'))

    def test_v2_unknown_trigger_is_preserved_by_refusing_rebuild(self):
        self.legacy(managed=True)
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute('CREATE TRIGGER custom_segment AFTER INSERT ON task_segments BEGIN SELECT 1; END')
        before = self.path.read_bytes()
        with self.assertRaisesRegex(TaskDataError, '自定义结构'):
            TaskRepository(self.path)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.version(), (2, '0002'))

    def partial_v3(self):
        """复制试用版 v3 的行为：仅执行分片和输出拆列，不调用剩余字段升级。"""
        location = self.root / 'partial-v3'
        shutil.copytree(migrations.SCRIPT_LOCATION, location, ignore=shutil.ignore_patterns('__pycache__'))
        (location / 'versions' / '0004_complete_columns.py').unlink()
        (location / 'versions' / '0005_hls_key_entries.py').unlink()
        (location / 'versions' / '0006_task_names_and_paths.py').unlink()
        (location / 'versions' / '0007_task_encryption.py').unlink()
        (location / 'versions' / '0008_remove_task_encryption.py').unlink()
        version = location / 'versions' / '0003_segment_output_columns.py'
        version.write_text(version.read_text(encoding='utf-8').replace('    upgrade_remaining()\n', ''),
                           encoding='utf-8')
        with patch.object(migrations, 'SCRIPT_LOCATION', location):
            TaskRepository(self.path)
        self.assertEqual(self.version(), (3, '0003'))

    def test_trial_v3_database_automatically_finishes_remaining_columns(self):
        tasks = self.legacy(managed=True)
        self.partial_v3()
        before = self.snapshot()
        for row in before['task_segments']:
            row.update(entry_type='segment', encryption=None)
        repo = TaskRepository(self.path)
        self.assertEqual(self.version(), (8, '0008'))
        for task in tasks:
            self.assertEqual(repo.get(task.id).type, task.type)
        self.assertEqual(len(list((self.root / 'backups').glob('*.db'))), 2)
        with closing(sqlite3.connect(self.path)) as connection:
            for table in ('task_m3u8', 'task_mp4', 'task_rtmp', 'task_segments', 'task_outputs'):
                self.assertNotIn('data', [row[1] for row in connection.execute(f'PRAGMA table_info({table})')])
            self.assertNotIn('progress', [row[1] for row in connection.execute('PRAGMA table_info(tasks)')])

    def test_remaining_details_and_progress_keep_values_and_support_updates(self):
        from datetime import datetime, timezone, timedelta
        tasks = self.legacy(managed=True)
        tasks[0].details.request_headers = {'Referer': 'https://example.com/?a=1&b=2', 'Cookie': 'session=a=b; quoted="x"; latin=é'}
        tasks[0].details.detect_duration = True
        tasks[0].details.work_dir_missing = True
        tasks[0].details.ts_pattern = 'https://example.com/{index}.ts'
        tasks[0].progress = TaskProgress(completed_segments=1, total_segments=2, downloaded_bytes=128, total_bytes=256)
        tasks[1].details.request_headers = {'Referer': 'https://example.com/mp4'}
        tasks[1].details.file_missing = True
        tasks[1].details.etag = ''
        tasks[1].details.last_modified = 'Mon, 19 Oct 2026 01:02:03 GMT'
        tasks[1].details.supports_ranges = False
        tasks[1].details.verified_bytes = 0
        tasks[1].progress = TaskProgress(total_bytes=0)
        tasks[2].details.recording_started_at = datetime(2026, 10, 19, 10, 20, tzinfo=timezone(timedelta(hours=5, minutes=30)))
        tasks[2].progress = TaskProgress(downloaded_bytes=100, recorded_seconds=12.75)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            for task in tasks:
                data = task.model_dump(mode='json')
                details = data['details']
                details.pop('segments', None)
                details.pop('detecting', None)
                if task.type.value in ('mp4', 'rtmp'):
                    details['target_path'] = task.outputs[0].output_path
                connection.execute(f'UPDATE task_{task.type.value} SET data=? WHERE task_id=?',
                                   (json.dumps(details), str(task.id)))
                connection.execute('UPDATE tasks SET progress=? WHERE id=?', (json.dumps(data['progress']), str(task.id)))
        before = self.snapshot()
        repo = TaskRepository(self.path)
        migrated_hls = repo.get(tasks[0].id)
        self.assertEqual(migrated_hls.name, 'video')
        self.assertEqual(migrated_hls.details.playlist_path, 'download.m3u8')
        self.assertEqual(migrated_hls.details.segments[0].segment_path, 'segments/000000.ts')
        self.assertEqual(repo.get(tasks[1].id).name, 'a')
        self.assertEqual(repo.get(tasks[2].id).name, 'live')
        self.assertNotEqual(self.snapshot(), before)  # 0006 normalizes names and M3U8 path bases.
        def update_hls(task):
            task.details.request_headers['Cookie'] = 'new-cookie'
            task.details.detect_duration = False
            task.progress.downloaded_bytes = 256
        saved = repo.mutate(tasks[0].id, update_hls)
        self.assertEqual(TaskRepository(self.path).get(saved.id), saved)
        with closing(sqlite3.connect(self.path)) as connection:
            headers, detect, missing = connection.execute(
                'SELECT request_headers, detect_duration, work_dir_missing FROM task_m3u8').fetchone()
            self.assertEqual(json.loads(headers)['Cookie'], 'new-cookie')
            self.assertEqual((detect, missing), (0, 1))
            self.assertEqual(connection.execute(
                'SELECT supports_ranges, verified_bytes, etag FROM task_mp4').fetchone(), (0, 0, ''))
            self.assertEqual(connection.execute(
                'SELECT recorded_seconds, total_bytes FROM tasks WHERE id=?', (str(tasks[2].id),)).fetchone(), (12.75, None))

    def test_remaining_invalid_json_rolls_back_trial_v3_parent_changes(self):
        self.legacy(managed=True)
        self.partial_v3()
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("UPDATE task_mp4 SET data='{broken'")
        before = self.path.read_bytes()
        with self.assertRaisesRegex(TaskDataError, '数据异常'):
            TaskRepository(self.path)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.version(), (3, '0003'))

    def test_missing_file_flags_remain_unchecked_until_service_reconciles(self):
        tasks = self.legacy(managed=True)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            for table, flag in (('task_m3u8', 'work_dir_missing'), ('task_mp4', 'file_missing')):
                data = json.loads(connection.execute(f'SELECT data FROM {table}').fetchone()[0])
                data.pop(flag)
                connection.execute(f'UPDATE {table} SET data=?', (json.dumps(data),))
        repo = TaskRepository(self.path)
        for task, flag, table in ((tasks[0], 'work_dir_missing', 'task_m3u8'), (tasks[1], 'file_missing', 'task_mp4')):
            restored = repo.get(task.id)
            self.assertNotIn(flag, restored.details.model_fields_set)
            self.assertFalse(getattr(restored.details, flag))
            with closing(sqlite3.connect(self.path)) as connection:
                self.assertIsNone(connection.execute(f'SELECT {flag} FROM {table}').fetchone()[0])


if __name__ == '__main__':
    unittest.main()
