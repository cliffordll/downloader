"""共享保存目录、名称分配与完整文件发布的回归检查。"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
import sqlite3
import unittest
from unittest.mock import patch

from src.core.path_manager import PathManager
from src.core.task_service import TaskService
from src.storage.task_repository import TaskRepository
from src.schemas.task import SourceType


CONTENT = '#EXTM3U\n#EXTINF:4,\nhttps://example.com/a.ts?token=x\n'


class DownloadPathsTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.repo = TaskRepository(self.root / 'tasks.db')
        self.service = TaskService(self.repo)
        self.folder = self.root / 'downloads'

    def test_url_name_excludes_query_without_changing_download_url(self):
        url = 'https://example.com/%E8%A7%86%E9%A2%91.mp4?token=secret&name=other'
        task = self.service.create_mp4(self.folder, url)
        self.assertEqual(task.name, '视频.mp4')
        self.assertEqual(task.save_dir, self.folder)
        self.assertEqual(task.source_url, url)
        self.assertTrue((self.folder / '视频.mp4.part').is_file())
        self.assertFalse((self.folder / '视频').exists())
        self.assertEqual(PathManager.GetFileNameFromURI('https://host/movie.m3u8?q=1'), 'movie.mp4')

    def test_mixed_types_allocate_files_and_work_directories_together(self):
        self.folder.mkdir()
        (self.folder / 'movie.mp4').write_bytes(b'keep')
        (self.folder / 'movie (1)').mkdir()
        (self.folder / 'movie (2).mp4.part').write_bytes(b'partial')
        mp4 = self.service.create_mp4(self.folder, 'https://host/movie.mp4')
        mu = self.service.create_m3u8(self.folder, 'https://host/movie.m3u8', CONTENT)
        ts = self.service.create_m3u8(self.folder, '', CONTENT, filename='MOVIE',
                                      source_type=SourceType.TS_PATTERN)
        self.assertEqual(mp4.name, 'movie (3).mp4')
        self.assertEqual(mu.name, 'movie (4).mp4')
        self.assertEqual(ts.name, 'MOVIE (5).mp4')
        self.assertEqual(mu.details.playlist_path, 'movie (4)/download.m3u8')
        self.assertEqual(mu.details.segments[0].relative_path, 'movie (4)/segments/000000.ts')
        text = (mu.save_dir / mu.details.playlist_path).read_text()
        self.assertIn('\nsegments/000000.ts\n', text)
        self.assertNotIn('movie (4)/segments', text)
        self.assertEqual(mu.outputs[0].relative_path, 'movie (4).mp4')
        self.assertEqual((self.folder / 'movie.mp4').read_bytes(), b'keep')

    def test_record_reserves_name_after_work_files_removed(self):
        task = self.service.create_mp4(self.folder, 'https://host/movie.mp4')
        (task.save_dir / task.details.temporary_path).unlink()
        other = self.service.create_m3u8(self.folder, 'https://host/movie.m3u8', CONTENT)
        self.assertEqual(other.name, 'movie (1).mp4')

    def test_concurrent_services_choose_distinct_names(self):
        def create(_):
            return TaskService(TaskRepository(self.repo.path)).create_mp4(self.folder, 'https://host/movie.mp4')
        with ThreadPoolExecutor(max_workers=4) as pool:
            tasks = list(pool.map(create, range(8)))
        self.assertEqual(len({task.name for task in tasks}), 8)
        self.assertEqual(len(self.repo.list_tasks()), 8)

    def test_invalid_file_names_and_nested_work_directory_are_rejected(self):
        for name in ['../x', 'a/b', 'a\\b', 'NUL', 'bad?', '.mp4']:
            with self.subTest(name=name), self.assertRaises(ValueError):
                PathManager.ValidateFileName(name)
        task = self.service.create_m3u8(self.folder, 'https://host/movie.m3u8', CONTENT)
        with self.assertRaises(ValueError):
            self.service.create_mp4(self.folder / 'movie', 'https://host/video.mp4')
        self.assertEqual(len(self.repo.list_tasks()), 1)

    def test_database_failure_cleans_only_allocated_artifacts(self):
        self.folder.mkdir()
        keep = self.folder / 'keep.txt'
        keep.write_text('keep')
        for kind in ['mp4', 'm3u8']:
            with patch.object(self.repo, '_write_children', side_effect=sqlite3.OperationalError('full')):
                with self.assertRaises(sqlite3.OperationalError):
                    if kind == 'mp4':
                        self.service.create_mp4(self.folder, 'https://host/movie.mp4')
                    else:
                        self.service.create_m3u8(self.folder, 'https://host/movie.m3u8', CONTENT)
            self.assertEqual(list(self.folder.iterdir()), [keep])
            self.assertEqual(self.repo.list_tasks(), [])

    def test_publish_never_overwrites_existing_file(self):
        temporary, target = self.root / 'video.part', self.root / 'video.mp4'
        temporary.write_bytes(b'new')
        target.write_bytes(b'existing')
        with self.assertRaises(FileExistsError):
            PathManager.PublishFile(temporary, target)
        self.assertEqual(target.read_bytes(), b'existing')
        self.assertEqual(temporary.read_bytes(), b'new')
        target.unlink()
        PathManager.PublishFile(temporary, target)
        self.assertEqual(target.read_bytes(), b'new')
        self.assertFalse(temporary.exists())
