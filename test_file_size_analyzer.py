"""文件夹大小分析器 - 扫描逻辑测试

覆盖窗口程序里真正干活的扫描线程 AnalysisThread，
外加列表里那些"垫底行"（占位行）的行为 —— 它上面写的必须是真的项数，不能是假的"载入中"。

跑法：python -m unittest test_file_size_analyzer
"""

import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from file_size_gui import (AnalysisThread, FileNode, FolderSizeGUI, elide_middle,
                           mindmap_rows, squarify)


class StructureLayoutTests(unittest.TestCase):
    """结构图的排版算法：纯计算，不开窗口也能测。"""

    def test_squarify_fills_the_whole_area_and_stays_inside(self):
        items = [('a', 50), ('b', 30), ('c', 12), ('d', 5), ('e', 2), ('f', 1)]
        boxes = squarify(items, 0, 0, 400, 300)
        self.assertEqual(len(boxes), len(items))
        area = sum(w * h for _n, _x, _y, w, h in boxes)
        self.assertAlmostEqual(area, 400 * 300, delta=1.0)
        for _node, x, y, w, h in boxes:
            self.assertGreaterEqual(x, -0.01)
            self.assertGreaterEqual(y, -0.01)
            self.assertLessEqual(x + w, 400.01)
            self.assertLessEqual(y + h, 300.01)

    def test_squarify_keeps_big_ones_big(self):
        boxes = squarify([('大', 100), ('中', 30), ('小', 1)], 0, 0, 200, 200)
        area = {node: w * h for node, _x, _y, w, h in boxes}
        self.assertGreater(area['大'], area['中'])
        self.assertGreater(area['中'], area['小'])

    def test_squarify_handles_nothing_to_draw(self):
        self.assertEqual(squarify([], 0, 0, 100, 100), [])
        self.assertEqual(squarify([('a', 0)], 0, 0, 100, 100), [])

    def test_mindmap_centers_root_between_children(self):
        """根节点跟着孩子居中（经典导图摆法，开图整棵树就在画面正中）；
        底下的节点也夹在自己孩子们的正中间。"""
        folder = FileNode('a', 100, True,
                          [FileNode('a1', 60, False), FileNode('a2', 40, False)])
        rows = mindmap_rows(FileNode('root', 100, True, [folder]), 3, 0, 20)
        centre = {node.name: y for node, _level, y in rows}
        self.assertAlmostEqual(centre['root'], (centre['a1'] + centre['a2']) / 2.0)
        self.assertLess(centre['a1'], centre['a'])
        self.assertLess(centre['a'], centre['a2'])
        self.assertEqual([node.name for node, _l, _y in rows][0], 'root')   # 父在前

    def test_mindmap_root_stays_visible_when_alone(self):
        """根下头啥都没有（收起了 / 到层了）：根也得有地方站，别算出负数坐标。"""
        rows = mindmap_rows(FileNode('root', 100, True, []), 3, 0, 20)
        self.assertEqual(rows[0][2], 10)                            # 中心 = 一半行高
        self.assertEqual([node.name for node, _l, _y in rows], ['root'])

    def test_mindmap_stops_at_the_depth_limit(self):
        mid = FileNode('mid', 1, True, [FileNode('leaf', 1, False)])
        top = FileNode('top', 1, True, [mid])
        rows = mindmap_rows(top, 1, 0, 20)
        self.assertEqual([node.name for node, _l, _y in rows], ['top', 'mid'])

    def test_mindmap_drops_what_is_too_small(self):
        root = FileNode('root', 101, True, [FileNode('big', 100, False),
                                            FileNode('tiny', 1, False)])
        names = [node.name for node, _l, _y in mindmap_rows(root, 3, 10, 20)]
        self.assertIn('big', names)
        self.assertNotIn('tiny', names)


class ElideTests(unittest.TestCase):
    def test_short_text_is_untouched(self):
        self.assertEqual(elide_middle('D:\\a\\b.txt', 40), 'D:\\a\\b.txt')

    def test_long_path_keeps_head_and_tail(self):
        """路径太长从中间掐：头上的盘符和尾上的文件名都得留着。"""
        text = 'D:\\' + ('很长的目录名\\' * 20) + 'ml.pak'
        out = elide_middle(text, 40)
        self.assertLessEqual(len(out), 40)
        self.assertTrue(out.startswith('D:\\'))
        self.assertTrue(out.endswith('ml.pak'))
        self.assertIn('…', out)


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

    def test_batches_report_child_counts(self):
        """每一批都要带上"这个文件夹里数到几项" —— 界面靠它在占位行上写真数字。"""
        with tempfile.TemporaryDirectory() as tmp:
            root_path = Path(tmp)
            self.make_tree(root_path)
            scanner = AnalysisThread()
            scanner.start(root_path)
            counts = {}
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                for event in scanner.drain_events():
                    if event['type'] == 'batch':
                        for parent_path, number in event['counts'].items():
                            counts[parent_path] = counts.get(parent_path, 0) + number
                if not scanner.running:
                    break
                time.sleep(0.01)
            for event in scanner.drain_events():
                if event['type'] == 'batch':
                    for parent_path, number in event['counts'].items():
                        counts[parent_path] = counts.get(parent_path, 0) + number

            result = scanner.result
            self.assertEqual(counts[()], len(result.children))
            self.assertEqual(counts[('nested',)], 1)
            self.assertEqual(counts[('node_modules',)], 1)


class PlaceholderRowTests(unittest.TestCase):
    """占位行的行为：它是给 Tk 画展开箭头用的垫底行，上面写的必须是真话。"""

    def setUp(self):
        try:
            self.gui = FolderSizeGUI()
        except Exception as exc:            # 没有桌面会话就开不了窗口
            self.skipTest(f"开不了窗口：{exc}")
        self.addCleanup(self.gui.root.destroy)
        self.gui.root.withdraw()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / 'sub').mkdir()
        (root / 'sub' / 'a.txt').write_bytes(b'a' * 10)
        (root / 'sub' / 'deep').mkdir()
        (root / 'sub' / 'deep' / 'b.txt').write_bytes(b'b' * 20)
        (root / 'empty').mkdir()
        (root / 'other').mkdir()
        (root / 'other' / 'e.txt').write_bytes(b'e' * 7)
        (root / 'top.txt').write_bytes(b'c' * 5)
        self.gui.start_analysis(str(root))
        self.pump()

    def pump(self, seconds=5.0):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.gui.root.update()
            if self.gui._finished and not self.gui.analysis.running and not self.gui._queue_entries:
                return
            time.sleep(0.005)
        self.fail("界面一直没干完")

    def placeholder_texts(self):
        return {path: self.gui.tree.item(item, 'text')
                for path, item in self.gui._placeholder_of.items()}

    def test_placeholder_shows_real_count_not_fake_loading(self):
        texts = self.placeholder_texts()
        self.assertNotIn('载入中…', set(texts.values()))
        self.assertEqual(texts.get(('sub',)), '2 项')     # 里面是 a.txt + deep
        self.assertNotIn(('empty',), texts)               # 空文件夹不该垫占位行

    def test_open_folder_drops_its_placeholder(self):
        """点开文件夹：真内容摆上来，占位行撤掉；焦点停在占位行上也不该卡住。"""
        item = self.gui.path_to_item[('sub',)]
        placeholder = self.gui._placeholder_of[('sub',)]
        self.gui.tree.focus(placeholder)
        self.gui._last_click_item = placeholder
        self.gui.tree.item(item, open=True)
        self.gui.on_tree_open(None)
        self.pump()

        children = [self.gui.item_to_path.get(row)
                    for row in self.gui.tree.get_children(item)]
        self.assertIn(('sub', 'a.txt'), children)
        self.assertIn(('sub', 'deep'), children)
        self.assertNotIn(('sub',), self.gui._placeholder_of)

    def test_open_loads_even_when_the_bookkeeping_is_wrong(self):
        """账记错了、焦点又停在占位行上，照样得把里面摆出来。

        这是"点开了却只显示占位行"的病根：收起来时认错行，账上一直挂着"已经展开过"。
        """
        item = self.gui.path_to_item[('sub',)]
        placeholder = self.gui._placeholder_of[('sub',)]
        self.gui._open_paths.add(('sub',))        # 假账：账上以为它早就展开过了
        self.gui.tree.focus(placeholder)          # 焦点被它底下那条占位行占着
        self.gui._last_click_item = ''            # 点箭头那一下没被记上
        self.gui.tree.item(item, open=True)
        self.gui.on_tree_open(None)
        self.pump()

        children = [self.gui.item_to_path.get(row)
                    for row in self.gui.tree.get_children(item)]
        self.assertIn(('sub', 'a.txt'), children)
        self.assertIn(('sub', 'deep'), children)

    def test_closing_another_folder_does_not_scratch_out_an_open_one(self):
        """收 A 的时候焦点停在开着的 B 上，不能把 B 从账上抹掉（抹掉 B 以后点开就没反应了）。"""
        sub_item = self.gui.path_to_item[('sub',)]
        other_item = self.gui.path_to_item[('other',)]
        self.gui._last_click_item = ''
        self.gui.tree.item(sub_item, open=True)
        self.gui.on_tree_open(None)
        self.pump()
        self.assertIn(('sub',), self.gui._open_paths)

        self.gui.tree.focus(sub_item)          # 焦点还停在开着的 sub 上
        self.gui.tree.item(other_item, open=False)
        self.gui.on_tree_close(None)

        self.assertIn(('sub',), self.gui._open_paths)

    def test_click_watchdog_fills_an_open_but_empty_folder(self):
        """最坏情况：行被点开了，但"点开"的事件压根没发出来 —— 手点一下的兜底也得把内容摆上。"""
        item = self.gui.path_to_item[('sub',)]
        self.gui.tree.item(item, open=True)      # 行是开着的……
        self.gui._last_click_item = ''
        self.gui.after_tree_click(None)          # ……但只走"点完自查"这条路，没有任何"点开"事件
        self.pump()

        children = [self.gui.item_to_path.get(row)
                    for row in self.gui.tree.get_children(item)]
        self.assertIn(('sub', 'a.txt'), children)
        self.assertIn(('sub', 'deep'), children)

    def test_status_bar_tells_you_where_a_row_lives(self):
        """层级深的时候，点一行就得知道它在谁的里面 —— 状态栏写完整路径。"""
        self.gui.tree.item(self.gui.path_to_item[('sub',)], open=True)
        self.gui.on_tree_open(None)
        self.pump()
        row = self.gui.path_to_item[('sub', 'a.txt')]
        self.gui.tree.selection_set(row)
        self.gui.on_tree_select(None)

        text = self.gui.path_label.cget('text')
        self.assertIn('sub', text)
        self.assertIn('a.txt', text)
        self.assertIn(Path(self.tmp.name).name, text)     # 根目录也得在里头

    def test_structure_window_opens_draws_and_drills(self):
        """结构图窗口：两种模式都画得出东西，能钻进去也能退回来。"""
        self.gui.open_structure()
        view = self.gui.structure_window
        self.assertIsNotNone(view)
        view.win.update()
        view._render()
        self.assertGreater(len(view._block_nodes), 0, "方块图什么都没画出来")

        view.set_mode('mindmap')
        view._render()
        self.assertGreater(len(view._block_nodes), 0, "思维导图什么都没画出来")

        started_at = view.current()
        child = next((node for node in started_at.children if node.is_dir), None)
        self.assertIsNotNone(child)
        view.stack.append(child)
        view._render()
        self.assertIs(view.current(), child)
        view.go_back()
        self.assertIs(view.current(), started_at)

        view.close()
        self.assertIsNone(self.gui.structure_window)

    def test_wheel_zoom_actually_moves_the_picture(self):
        """滚一格滚轮，画面 16 毫秒内就得真的变大。

        光改倍数数字、画面纹丝不动是翻过车的（滚了没反应、停手才跳一下），
        这条测试专防这个：不调 _apply_zoom，就等定时器自己落地，量方块宽没宽。
        """
        self.gui.open_structure()
        view = self.gui.structure_window
        view.win.update()
        view.set_mode('mindmap')
        view.win.update()
        before = view._block_rects[id(view.current())]
        event = SimpleNamespace(delta=120, x=view.canvas.winfo_width() // 2,
                                y=view.canvas.winfo_height() // 2)
        view._on_wheel(event)
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and view._zoom_job is not None:
            view.win.update()
            time.sleep(0.004)
        view.win.update()
        self.assertIsNone(view._zoom_job, "缩放的定时器一直没落地")
        after = view._block_rects[id(view.current())]
        self.assertGreater(after[2], before[2] * 1.05, "滚了滚轮画面没变大")
        view.close()

    def test_panning_into_undrawn_area_repaints(self):
        """眼前这屏要是没画过，补画一把就得铺满 —— 这是拖画布不露白的兜底。"""
        self.gui.open_structure()
        view = self.gui.structure_window
        view.win.update()
        view.set_mode('mindmap')
        view.win.update()
        # 假装"上次画的是十万八千里外"：把已画范围整个挪走，眼前这屏就是没画过的
        view._drawn_top = view._drawn_bottom = view.canvas.canvasy(0) + 10 ** 6
        self.assertFalse(view._viewport_covered(), "已画范围都挪走了居然还算'画过了'")
        view._pan_repaint()
        self.assertTrue(view._viewport_covered(), "补画完眼前还是没铺满")
        view.close()

    def test_expand_all_and_search_leave_no_placeholder_behind(self):
        self.gui.expand_all()
        self.pump()
        self.gui.search_entry.insert(0, 'b.txt')
        self.gui.apply_search()
        self.pump()
        rows = set(self.gui._placeholder_of)
        self.assertFalse(rows, f"还有占位行没撤干净：{rows}")
        for line in self.placeholder_texts().values():
            self.assertNotIn('载入中…', line)


if __name__ == '__main__':
    unittest.main()
