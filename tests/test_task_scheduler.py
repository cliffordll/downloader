"""用阻塞请求验证真实调度顺序与并发上限，不依赖网络速度。"""
from concurrent.futures import wait
import threading
import time
import unittest
from unittest.mock import patch

from src.core.sys_setting import SysSetting
from src.media.downloader import Downloader


class TaskSchedulerTests(unittest.TestCase):
    def setUp(self):
        self.settings = dict(SysSetting.Defaults(), max_tasks=2, max_workers=2)
        self.patch = patch.object(SysSetting, 'GetAll', side_effect=lambda: dict(self.settings))
        self.patch.start()
        self.gates = []
        self.futures = []

    def tearDown(self):
        for gate in self.gates:
            gate.set()
        for future in self.futures:
            future.cancel()
        wait(self.futures, timeout=3)
        thread = Downloader._thread
        if thread:
            thread.join(3)
            self.assertFalse(thread.is_alive())
        self.patch.stop()

    def gate(self):
        gate = threading.Event()
        self.gates.append(gate)
        return gate

    def submit(self, task, function, **kwargs):
        future = Downloader.Submit(task, function, **kwargs)
        self.futures.append(future)
        return future

    def until(self, condition):
        deadline = time.monotonic() + 3
        while not condition():
            if time.monotonic() > deadline:
                self.fail('调度状态未在期限内更新')
            time.sleep(0.01)

    def test_two_tasks_each_get_their_own_concurrency_and_third_waits(self):
        gates = {task: self.gate() for task in ('a', 'b', 'c')}
        started = {task: 0 for task in gates}
        lock = threading.Lock()

        def request(task):
            with lock:
                started[task] += 1
            gates[task].wait(5)

        for task in gates:
            for _ in range(3):
                self.submit(task, lambda task=task: request(task))
        self.until(lambda: started['a'] == started['b'] == 2)
        time.sleep(0.1)
        self.assertEqual(started, dict(a=2, b=2, c=0))
        gates['a'].set()
        self.until(lambda: started['c'] == 2)
        self.assertEqual(started['a'], 3)
        self.assertEqual(started['b'], 2)

    def test_paused_task_yields_after_running_request_then_resumes_at_tail(self):
        self.settings.update(max_tasks=1, max_workers=1)
        release_a, release_b, release_c = self.gate(), self.gate(), self.gate()
        ready = self.gate()
        ready.set()
        started = []
        self.submit('a', lambda: (started.append('a1'), release_a.wait(5)))
        self.until(lambda: started == ['a1'])
        self.submit('a', lambda: started.append('a2'), ready=ready.is_set)
        self.submit('b', lambda: (started.append('b'), release_b.wait(5)))
        ready.clear()
        time.sleep(0.1)
        self.assertEqual(started, ['a1'])
        release_a.set()
        self.until(lambda: 'b' in started)
        self.submit('c', lambda: (started.append('c'), release_c.wait(5)))
        ready.set()
        release_b.set()
        self.until(lambda: 'c' in started)
        self.assertEqual(started, ['a1', 'b', 'c'])
        release_c.set()
        self.until(lambda: 'a2' in started)

    def test_cancelled_waiting_task_never_starts_and_failure_frees_slot(self):
        self.settings.update(max_tasks=1, max_workers=1)
        release, started = self.gate(), self.gate()

        def fail():
            started.set()
            release.wait(5)
            raise ValueError('failed')

        first = self.submit('a', fail)
        self.assertTrue(started.wait(2))
        calls = []
        cancelled = self.submit('b', lambda: calls.append('b'))
        cancelled.cancel()
        last = self.submit('c', lambda: calls.append('c'))
        release.set()
        with self.assertRaises(ValueError):
            first.result(3)
        last.result(3)
        self.assertEqual(calls, ['c'])
        self.assertFalse(wait([cancelled], timeout=1).not_done)

    def test_mp4_limit_is_one_and_settings_changes_apply_to_future_requests(self):
        self.settings.update(max_tasks=1, max_workers=3)
        gate = self.gate()
        calls = []
        for _ in range(3):
            self.submit('mp4', lambda: (calls.append('mp4'), gate.wait(5)), limit=1)
        for _ in range(4):
            self.submit('ts', lambda: (calls.append('ts'), gate.wait(5)))
        self.until(lambda: calls == ['mp4'])
        self.settings['max_tasks'] = 2
        self.until(lambda: calls.count('ts') == 3)
        self.assertEqual(calls.count('mp4'), 1)
        self.settings['max_workers'] = 1
        time.sleep(0.1)
        self.assertEqual(calls.count('ts'), 3)
        gate.set()
        self.assertFalse(wait(self.futures, timeout=3).not_done)
        self.assertEqual(calls.count('ts'), 4)
