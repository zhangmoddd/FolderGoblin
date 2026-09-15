"""文件夹大小分析器 - 扫描逻辑测试

只覆盖窗口程序里真正干活的扫描线程 AnalysisThread。
改完 file_size_gui.py 之后跑一下这个，确认没扫坏。

跑法：python -m unittest test_file_size_analyzer
"""

import tempfile
import time
import unittest
from pathlib import Path

from file_size_gui import AnalysisThread


class ScannerTests(unittest.TestCase):
    def make_tree(self, root: Path):
        (root / 'small.txt').write_bytes(b'a' * 10)
        (root / 'nested').mkdir()
        (root / 'nested' / 'large.bin').write_bytes(b'b' * 30)
        # 以前这几类目录会被"忽略名单"静默跳过，现在必须一个不落地算进去
        for name in ('node_modules', '.git', 'build', '__pycache__'):
            (root / name).mkdir()
            (root / name / 'payload.bin').write_bytes(b'x' * 100)

    def test_scanner_streams_batches_and_final_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            root_path = Path(tmp)
            self.make_tree(root_path)
            scanner = AnalysisThread()
            generation = scanner.start(root_path)
            deadline = time.monotonic() + 5
            events = []
            while time.monotonic() < deadline:
                events.extend(scanner.drain_events())
                if not scanner.running:
                    events.extend(scanner.drain_events())
                    break
                time.sleep(0.01)

            self.assertFalse(scanner.running)
            batches = [event for event in events if event['type'] == 'batch']
            completed = [event for event in events if event['type'] == 'complete']
            self.assertTrue(batches)
            self.assertEqual(len(completed), 1)
            self.assertEqual(completed[0]['generation'], generation)
            self.assertFalse(completed[0]['cancelled'])
            self.assertEqual(completed[0]['root'].size, 440)
            self.assertEqual(completed[0]['files'], 6)
            self.assertGreaterEqual(completed[0]['directories'], 6)

    def test_nothing_is_ignored(self):
        """忽略名单必须彻底不存在：node_modules / .git / build / __pycache__ 全都要算。"""
        with tempfile.TemporaryDirectory() as tmp:
            root_path = Path(tmp)
            self.make_tree(root_path)
            scanner = AnalysisThread()
            scanner.start(root_path)
            deadline = time.monotonic() + 5
            while scanner.running and time.monotonic() < deadline:
                time.sleep(0.01)

            self.assertIsNotNone(scanner.result)
            names = {child.name for child in scanner.result.children}
            self.assertEqual(
                names & {'node_modules', '.git', 'build', '__pycache__'},
                {'node_modules', '.git', 'build', '__pycache__'},
            )
            self.assertEqual(scanner.symlinks_skipped, 0)

    def test_pause_uses_a_real_blocking_event(self):
        scanner = AnalysisThread()
        scanner.running = True
        scanner._pause_event.set()
        scanner.pause()
        self.assertTrue(scanner.paused)
        self.assertFalse(scanner._pause_event.is_set())
        scanner.resume()
        self.assertFalse(scanner.paused)
        self.assertTrue(scanner._pause_event.is_set())


if __name__ == '__main__':
    unittest.main()
