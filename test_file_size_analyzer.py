import tempfile
import time
import unittest
from pathlib import Path

from file_size_analyzer import analyze_directory, build_hierarchy, render_html
from file_size_gui import AnalysisThread


class AnalyzerTests(unittest.TestCase):
    def make_tree(self, root: Path):
        (root / 'small.txt').write_bytes(b'a' * 10)
        (root / 'nested').mkdir()
        (root / 'nested' / 'large.bin').write_bytes(b'b' * 30)
        (root / 'build').mkdir()
        (root / 'build' / 'ignored.bin').write_bytes(b'x' * 100)

    def test_cli_caches_aggregate_size_and_avoids_d3_double_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            root_path = Path(tmp)
            self.make_tree(root_path)
            root = analyze_directory(root_path, {'build'})

            self.assertEqual(root.size, 40)
            self.assertEqual(root.total_size(), 40)
            self.assertEqual([child.name for child in root.children], ['nested', 'small.txt'])

            hierarchy = build_hierarchy(root)
            self.assertEqual(hierarchy['size'], 0)

            def leaf_sum(node):
                return node['size'] + sum(leaf_sum(child) for child in node['children'])

            self.assertEqual(leaf_sum(hierarchy), root.size)

    def test_max_depth_does_not_add_nodes_beyond_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root_path = Path(tmp)
            self.make_tree(root_path)
            root = analyze_directory(root_path, {'build'}, max_depth=1)
            self.assertEqual(root.size, 10)
            nested = next(child for child in root.children if child.name == 'nested')
            self.assertEqual(nested.children, [])
            self.assertEqual(nested.size, 0)

    def test_gui_scanner_streams_batches_and_final_result(self):
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
            self.assertEqual(completed[0]['root'].size, 40)
            self.assertEqual(completed[0]['files'], 2)
            self.assertGreaterEqual(completed[0]['directories'], 2)

    def test_hidden_toggle_and_html_special_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            root_path = Path(tmp)
            (root_path / '.hidden').write_bytes(b'hidden')

            hidden_ignored = analyze_directory(root_path, {'.'})
            self.assertNotIn('.hidden', [child.name for child in hidden_ignored.children])
            hidden_included = analyze_directory(root_path, set())
            self.assertIn('.hidden', [child.name for child in hidden_included.children])

            special_root = type(hidden_included)(name='<script>&\"', size=4, is_dir=True, children=[
                type(hidden_included)(name='<script>&\".txt', size=4, is_dir=False, children=[])
            ])
            output = root_path / 'result.html'
            self.assertTrue(render_html(special_root, str(output)))
            content = output.read_text(encoding='utf-8')
            self.assertIn(r'\u003cscript\u003e', content)
            self.assertNotIn('<script>&".txt', content)

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
