#!/usr/bin/env python3
"""
文件夹大小分析器 - 霓虹风格 GUI (Treeview版本，修复滚动性能)
"""

import tkinter as tk
from tkinter import ttk, filedialog
import threading
import os
import queue
import time
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Callable

IGNORE_DEFAULT = {"node_modules", ".git", "__pycache__", ".vscode", "bin", "obj", ".idea", "dist", "build"}


@dataclass
class FileNode:
    name: str
    size: int
    is_dir: bool
    children: List['FileNode'] = field(default_factory=list)

    def total_size(self) -> int:
        """Return the cached aggregate size (O(1) for directories)."""
        return self.size


def format_size(bytes_size: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    unit_idx = 0
    size = float(max(0, bytes_size))
    while size >= 1024.0 and unit_idx < len(units) - 1:
        size /= 1024.0
        unit_idx += 1
    if unit_idx == 0:
        return f"{int(size)} B"
    return f"{size:.2f} {units[unit_idx]}"


class AnalysisThread:
    """Fast scanner that sends immutable batches to the Tk main thread."""

    BATCH_ENTRY_LIMIT = 256
    BATCH_INTERVAL = 0.12

    def __init__(self):
        self.running = False
        self.paused = False
        self.thread: Optional[threading.Thread] = None
        self.result: Optional[FileNode] = None
        self.files_scanned = 0
        self.directories_scanned = 0
        self.bytes_scanned = 0
        self.errors = 0
        self.generation = 0
        self.events = queue.SimpleQueue()
        self._pause_event = threading.Event()
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._pending_entries = []
        self._pending_deltas = {}
        self._last_flush = 0.0

    def start(self, path: Path) -> int:
        self.stop()
        old_thread = self.thread
        if old_thread and old_thread.is_alive():
            old_thread.join(timeout=0.5)

        with self._lock:
            self.generation += 1
            generation = self.generation
            self.running = True
            self.paused = False
            self.result = None
            self.files_scanned = 0
            self.directories_scanned = 0
            self.bytes_scanned = 0
            self.errors = 0
            self.events = queue.SimpleQueue()
            self._pending_entries = []
            self._pending_deltas = {}
            self._last_flush = time.monotonic()
            self._stop_event.clear()
            self._pause_event.set()

        self.thread = threading.Thread(
            target=self._analyze,
            args=(Path(path), generation),
            name=f"folder-scan-{generation}",
            daemon=True,
        )
        self.thread.start()
        return generation

    def _is_active(self, generation: int) -> bool:
        return generation == self.generation and not self._stop_event.is_set()

    def _wait_if_paused(self, generation: int) -> bool:
        while self._is_active(generation):
            if self._pause_event.wait(timeout=0.1):
                return True
        return False

    def _queue_entry(self, parent_path, node_path, name, size, is_dir, generation):
        if generation != self.generation:
            return
        self._pending_entries.append((parent_path, node_path, name, size, is_dir))
        if not is_dir:
            self._pending_deltas[parent_path] = self._pending_deltas.get(parent_path, 0) + size
        now = time.monotonic()
        if len(self._pending_entries) >= self.BATCH_ENTRY_LIMIT or now - self._last_flush >= self.BATCH_INTERVAL:
            self._flush_pending(generation)

    def _flush_pending(self, generation: int):
        if generation != self.generation or (not self._pending_entries and not self._pending_deltas):
            return
        event = {
            'type': 'batch',
            'generation': generation,
            'entries': self._pending_entries,
            'deltas': self._pending_deltas,
            'files': self.files_scanned,
            'directories': self.directories_scanned,
            'bytes': self.bytes_scanned,
            'errors': self.errors,
        }
        self._pending_entries = []
        self._pending_deltas = {}
        self._last_flush = time.monotonic()
        self.events.put(event)

    def _analyze(self, path: Path, generation: int):
        self.directories_scanned = 1
        root = self._scan_directory(path, (), generation)
        self._flush_pending(generation)
        cancelled = not self._is_active(generation)
        if generation == self.generation:
            self.result = root
            self.running = False
            self.paused = False
            self.events.put({
                'type': 'complete',
                'generation': generation,
                'root': root,
                'cancelled': cancelled,
                'files': self.files_scanned,
                'directories': self.directories_scanned,
                'bytes': self.bytes_scanned,
                'errors': self.errors,
            })

    def _scan_directory(self, path: Path, relative_path, generation: int) -> FileNode:
        node = FileNode(name=path.name or str(path), size=0, is_dir=True, children=[])
        try:
            with os.scandir(path) as entries:
                for entry in entries:
                    if not self._wait_if_paused(generation):
                        break
                    try:
                        name = entry.name
                        if name in IGNORE_DEFAULT or entry.is_symlink():
                            continue

                        child_path = relative_path + (name,)
                        if entry.is_dir(follow_symlinks=False):
                            self.directories_scanned += 1
                            self._queue_entry(relative_path, child_path, name, 0, True, generation)
                            child = self._scan_directory(Path(entry.path), child_path, generation)
                            node.children.append(child)
                            node.size += child.size
                        elif entry.is_file(follow_symlinks=False):
                            size = entry.stat(follow_symlinks=False).st_size
                            child = FileNode(name=name, size=size, is_dir=False)
                            node.children.append(child)
                            node.size += size
                            self.files_scanned += 1
                            self.bytes_scanned += size
                            self._queue_entry(relative_path, child_path, name, size, False, generation)
                    except (PermissionError, FileNotFoundError, OSError):
                        self.errors += 1
        except (PermissionError, FileNotFoundError, NotADirectoryError, OSError):
            self.errors += 1

        node.children.sort(key=lambda child: child.size, reverse=True)
        return node

    def drain_events(self, limit: int = 200):
        drained = []
        for _ in range(limit):
            try:
                drained.append(self.events.get_nowait())
            except queue.Empty:
                break
        return drained

    def pause(self):
        if self.running:
            self.paused = True
            self._pause_event.clear()

    def resume(self):
        if self.running:
            self.paused = False
            self._pause_event.set()

    def stop(self):
        self._stop_event.set()
        self.paused = False
        self._pause_event.set()


class NeonGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("文件夹大小分析器")
        self.root.geometry("1400x900")
        self.root.configure(bg='#0a0a0f')

        self.bg_dark = '#0a0a0f'
        self.bg_surface = '#12121a'
        self.bg_elevated = '#1a1a24'
        self.neon_cyan = '#00f5ff'
        self.neon_magenta = '#ff00aa'
        self.neon_lime = '#b8ff00'
        self.neon_orange = '#ff6b35'
        self.text_primary = '#ffffff'
        self.text_secondary = '#8a8a9a'

        self.analysis = AnalysisThread()
        self.current_root = None
        self.analyzed_path = None
        self.item_to_node: Dict[str, FileNode] = {}
        self.path_to_item = {}
        self.item_to_path = {}
        self.scan_generation = 0
        self._poll_job = None
        self.live_max_depth = 0

        self.setup_styles()
        self.create_widgets()
        self.create_context_menu()

    def setup_styles(self):
        style = ttk.Style()
        style.theme_use('clam')
        style.configure('Neon.TFrame', background=self.bg_surface)
        style.configure('Neon.Treeview',
            background=self.bg_surface,
            foreground=self.text_primary,
            fieldbackground=self.bg_surface,
            bordercolor=self.neon_cyan,
            rowheight=34,
            font=('Microsoft YaHei UI', 12)
        )
        style.configure('Neon.Treeview.Heading',
            background=self.bg_elevated,
            foreground=self.neon_cyan,
            font=('Microsoft YaHei UI', 12, 'bold')
        )

    def create_widgets(self):
        # Header
        header = tk.Frame(self.root, bg=self.bg_surface, padx=25, pady=15)
        header.pack(fill='x')
        tk.Label(header, text="文件夹大小分析器",
            font=('Microsoft YaHei UI', 22, 'bold'),
            fg=self.neon_cyan, bg=self.bg_surface).pack(side='left')

        # Stats bar
        stats_frame = tk.Frame(self.root, bg=self.bg_surface, padx=25, pady=12)
        stats_frame.pack(fill='x')
        self.total_label = tk.Label(stats_frame, text="总大小: --",
            font=('Microsoft YaHei UI', 14), fg=self.neon_lime, bg=self.bg_surface)
        self.total_label.pack(side='left', padx=25)
        self.count_label = tk.Label(stats_frame, text="文件/文件夹: --",
            font=('Microsoft YaHei UI', 14), fg=self.neon_magenta, bg=self.bg_surface)
        self.count_label.pack(side='left', padx=25)
        self.depth_label = tk.Label(stats_frame, text="最大深度: --",
            font=('Microsoft YaHei UI', 14), fg=self.neon_cyan, bg=self.bg_surface)
        self.depth_label.pack(side='left', padx=25)
        self.scan_label = tk.Label(stats_frame, text="",
            font=('Microsoft YaHei UI', 12), fg=self.neon_orange, bg=self.bg_surface)
        self.scan_label.pack(side='right', padx=10)

        # Control bar
        control_frame = tk.Frame(self.root, bg=self.bg_elevated, padx=25, pady=15)
        control_frame.pack(fill='x')
        self.browse_btn = tk.Button(control_frame, text="选择文件夹",
            command=self.browse_folder,
            bg=self.bg_elevated, fg=self.neon_cyan,
            font=('Microsoft YaHei UI', 13, 'bold'),
            relief='flat', bd=0, padx=20, pady=10,
            cursor='hand2',
            highlightthickness=1, highlightcolor=self.neon_cyan,
            highlightbackground=self.bg_elevated)
        self.browse_btn.pack(side='left', padx=5)
        self.pause_btn = tk.Button(control_frame, text="暂停",
            command=self.toggle_pause,
            bg=self.neon_orange, fg=self.bg_dark,
            font=('Microsoft YaHei UI', 13, 'bold'),
            relief='flat', bd=0, padx=20, pady=10,
            state='disabled', cursor='hand2')
        self.pause_btn.pack(side='left', padx=5)
        self.stop_btn = tk.Button(control_frame, text="停止",
            command=self.stop_analysis,
            bg='#e74c3c', fg=self.text_primary,
            font=('Microsoft YaHei UI', 13, 'bold'),
            relief='flat', bd=0, padx=20, pady=10,
            state='disabled', cursor='hand2')
        self.stop_btn.pack(side='left', padx=5)
        self.expand_btn = tk.Button(control_frame, text="展开全部",
            command=self.expand_all,
            bg=self.bg_elevated, fg=self.neon_cyan,
            font=('Microsoft YaHei UI', 12),
            relief='flat', bd=0, padx=15, pady=8,
            cursor='hand2')
        self.expand_btn.pack(side='left', padx=20)
        self.collapse_btn = tk.Button(control_frame, text="收起全部",
            command=self.collapse_all,
            bg=self.bg_elevated, fg=self.neon_cyan,
            font=('Microsoft YaHei UI', 12),
            relief='flat', bd=0, padx=15, pady=8,
            cursor='hand2')
        self.collapse_btn.pack(side='left', padx=5)

        # Search box
        search_frame = tk.Frame(control_frame, bg=self.bg_elevated)
        search_frame.pack(side='right', padx=5)
        tk.Label(search_frame, text="搜索:",
            fg=self.text_secondary, bg=self.bg_elevated,
            font=('Microsoft YaHei UI', 11)).pack(side='left', padx=(10, 5))
        self.search_entry = tk.Entry(search_frame,
            bg=self.bg_dark, fg=self.text_primary,
            font=('Microsoft YaHei UI', 12),
            insertbackground=self.neon_cyan,
            relief='flat', bd=0, width=20)
        self.search_entry.pack(side='left', padx=5)
        self.search_entry.bind('<KeyRelease>', lambda e: self.apply_search())
        tk.Button(search_frame, text="清除",
            command=self.clear_search,
            bg=self.bg_dark, fg=self.neon_cyan,
            font=('Microsoft YaHei UI', 10),
            relief='flat', bd=0, padx=10, pady=4,
            cursor='hand2').pack(side='left', padx=5)

        self.progress = ttk.Progressbar(control_frame, mode='indeterminate', length=200)

        # Main content
        content = tk.Frame(self.root, bg=self.bg_dark, padx=20, pady=15)
        content.pack(fill='both', expand=True)

        # Tree view with virtual scrolling
        tree_frame = tk.Frame(content, bg=self.bg_surface)
        tree_frame.pack(side='left', fill='both', expand=True)

        scroll_y = ttk.Scrollbar(tree_frame, orient='vertical')
        scroll_y.pack(side='right', fill='y')
        scroll_x = ttk.Scrollbar(tree_frame, orient='horizontal')
        scroll_x.pack(side='bottom', fill='x')

        self.tree = ttk.Treeview(tree_frame,
            style='Neon.Treeview',
            yscrollcommand=scroll_y.set,
            xscrollcommand=scroll_x.set)
        self.tree.pack(fill='both', expand=True)
        scroll_y.config(command=self.tree.yview)
        scroll_x.config(command=self.tree.xview)

        self.tree.tag_configure('folder', foreground=self.neon_cyan, font=('Microsoft YaHei UI', 12, 'bold'))
        self.tree.tag_configure('file', foreground=self.text_primary, font=('Microsoft YaHei UI', 11))
        self.tree.tag_configure('big', foreground=self.neon_lime, font=('Microsoft YaHei UI', 13, 'bold'))

        # Stats panel
        stats_panel = tk.Frame(content, bg=self.bg_surface, width=300)
        stats_panel.pack(side='right', fill='y', padx=(10, 0))
        stats_panel.pack_propagate(False)
        tk.Label(stats_panel, text="文件类型统计",
            font=('Microsoft YaHei UI', 14, 'bold'),
            fg=self.neon_cyan, bg=self.bg_surface).pack(pady=(10, 15))
        self.stats_text = tk.Text(stats_panel,
            bg=self.bg_surface, fg=self.text_primary,
            font=('Consolas', 12),
            relief='flat', bd=0,
            state='disabled', wrap='none')
        self.stats_text.pack(fill='both', expand=True, padx=10, pady=(0, 10))

        self.tree.bind("<Button-3>", self.show_context_menu)

        # Keyboard shortcuts
        self.root.bind('<Control-o>', lambda e: self.browse_folder())
        self.root.bind('<Control-O>', lambda e: self.browse_folder())
        self.root.bind('<Escape>', lambda e: self.stop_analysis() if self.analysis.running else None)
        self.root.bind('<Control-e>', lambda e: self.expand_all())
        self.root.bind('<Control-E>', lambda e: self.expand_all())
        self.root.bind('<Control-w>', lambda e: self.collapse_all())
        self.root.bind('<Control-W>', lambda e: self.collapse_all())
        self.root.bind('<Control-f>', lambda e: self.focus_search())
        self.root.bind('<Control-F>', lambda e: self.focus_search())
        self.root.bind('<Return>', lambda e: self.apply_search())

    def create_context_menu(self):
        self.context_menu = tk.Menu(self.tree, tearoff=0,
            bg=self.bg_elevated, fg=self.text_primary,
            font=('Microsoft YaHei UI', 11))
        self.context_menu.add_command(label="按大小排序（降序）", command=self.sort_by_size_desc)
        self.context_menu.add_command(label="按大小排序（升序）", command=self.sort_by_size_asc)
        self.context_menu.add_command(label="按名称排序（A-Z）", command=self.sort_by_name_az)
        self.context_menu.add_command(label="按名称排序（Z-A）", command=self.sort_by_name_za)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="展开全部", command=self.expand_all)
        self.context_menu.add_command(label="收起全部", command=self.collapse_all)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="复制名称", command=self.copy_name)
        self.context_menu.add_command(label="复制文件地址", command=self.copy_path)

    def browse_folder(self):
        folder = filedialog.askdirectory(title="选择要分析的文件夹")
        if folder:
            self.start_analysis(folder)

    def start_analysis(self, path):
        self.analyzed_path = path
        self.current_root = None
        self.tree.delete(*self.tree.get_children())
        self.item_to_node.clear()
        self.path_to_item.clear()
        self.item_to_path.clear()
        self.live_max_depth = 0
        root_node = FileNode(Path(path).name or str(path), 0, True)
        root_id = self.tree.insert('', 'end', text=f"{root_node.name}  0 B", values=(0, True), tags=('folder',), open=True)
        self.item_to_node[root_id] = root_node
        self.path_to_item[()] = root_id
        self.item_to_path[root_id] = ()

        self.total_label.config(text="总大小: 0 B")
        self.count_label.config(text="文件/文件夹: 1")
        self.depth_label.config(text="最大深度: 0")
        self.scan_label.config(text="正在扫描... (0 文件)")
        self.stats_text.config(state='normal')
        self.stats_text.delete(1.0, 'end')
        self.stats_text.config(state='disabled')
        self.browse_btn.config(state='disabled')
        self.pause_btn.config(state='normal', text="暂停")
        self.stop_btn.config(state='normal')
        self.progress.pack(side='left', padx=15)
        self.progress.start()

        self.scan_generation = self.analysis.start(Path(path))
        self._schedule_event_poll()

    def _schedule_event_poll(self):
        if self._poll_job is None:
            self._poll_job = self.root.after(60, self._poll_analysis_events)

    def _poll_analysis_events(self):
        self._poll_job = None
        events = self.analysis.drain_events()
        for event in events:
            if event.get('generation') != self.scan_generation:
                continue
            if event['type'] == 'batch':
                self._apply_live_batch(event)
            elif event['type'] == 'complete':
                self.on_analysis_complete(event)
        if self.analysis.running or events:
            self._schedule_event_poll()

    def _node_display(self, node: FileNode):
        return f"{node.name}  {format_size(node.size)}"

    def _node_tags(self, node: FileNode):
        if node.size >= 100 * 1024 * 1024:
            return ('big',)
        return ('folder' if node.is_dir else 'file',)

    def _apply_live_batch(self, event):
        for parent_path, node_path, name, size, is_dir in event['entries']:
            if node_path in self.path_to_item:
                continue
            parent_id = self.path_to_item.get(parent_path)
            if parent_id is None:
                continue
            node = FileNode(name=name, size=size, is_dir=is_dir)
            item_id = self.tree.insert(
                parent_id, 'end', text=self._node_display(node),
                values=(size, is_dir), tags=self._node_tags(node)
            )
            self.path_to_item[node_path] = item_id
            self.live_max_depth = max(self.live_max_depth, len(node_path))
            self.item_to_path[item_id] = node_path
            self.item_to_node[item_id] = node

        changed_paths = set()
        for parent_path, delta in event['deltas'].items():
            current = parent_path
            while True:
                item_id = self.path_to_item.get(current)
                if item_id:
                    node = self.item_to_node[item_id]
                    node.size += delta
                    changed_paths.add(current)
                if not current:
                    break
                current = current[:-1]

        for changed_path in changed_paths:
            item_id = self.path_to_item[changed_path]
            node = self.item_to_node[item_id]
            self.tree.item(item_id, text=self._node_display(node), tags=self._node_tags(node))
            self.tree.item(item_id, values=(node.size, node.is_dir))

        total_nodes = event['files'] + event['directories']
        max_depth = self.live_max_depth
        self.total_label.config(text=f"总大小: {format_size(event['bytes'])}")
        self.count_label.config(text=f"文件/文件夹: {total_nodes}")
        self.depth_label.config(text=f"最大深度: {max_depth}")
        error_text = f"，跳过 {event['errors']} 项" if event['errors'] else ""
        self.scan_label.config(text=f"正在扫描... ({event['files']} 文件{error_text})")

    def toggle_pause(self):
        if not self.analysis.running:
            return
        if self.analysis.paused:
            self.analysis.resume()
            self.pause_btn.config(text="暂停")
            self.scan_label.config(text=f"正在扫描... ({self.analysis.files_scanned} 文件)")
        else:
            self.analysis.pause()
            self.pause_btn.config(text="继续")
            self.scan_label.config(text=f"已暂停 ({self.analysis.files_scanned} 文件)")

    def stop_analysis(self):
        if not self.analysis.running:
            return
        self.analysis.stop()
        self.pause_btn.config(state='disabled', text="暂停")
        self.stop_btn.config(state='disabled')
        self.scan_label.config(text="正在停止...")
        self._schedule_event_poll()

    def on_analysis_complete(self, event):
        self.progress.stop()
        self.progress.pack_forget()
        self.browse_btn.config(state='normal')
        self.pause_btn.config(state='disabled', text="暂停")
        self.stop_btn.config(state='disabled')
        root = event['root']
        self.current_root = root
        self._adopt_final_tree(root)
        status = "已停止" if event['cancelled'] else "扫描完成"
        error_text = f"，跳过 {event['errors']} 项" if event['errors'] else ""
        self.scan_label.config(text=f"{status} ({event['files']} 文件{error_text})")

    def _adopt_final_tree(self, root):
        node_count = 0
        max_depth = 0
        self.item_to_node.clear()

        def adopt(node, rel_path, depth):
            nonlocal node_count, max_depth
            node_count += 1
            max_depth = max(max_depth, depth)
            item_id = self.path_to_item.get(rel_path)
            if item_id:
                self.item_to_node[item_id] = node
                self.tree.item(item_id, text=self._node_display(node), tags=self._node_tags(node))
                self.tree.item(item_id, values=(node.size, node.is_dir))
                for index, child in enumerate(node.children):
                    child_path = rel_path + (child.name,)
                    child_id = self.path_to_item.get(child_path)
                    if child_id:
                        self.tree.move(child_id, item_id, index)
                    adopt(child, child_path, depth + 1)

        adopt(root, (), 0)
        self.total_label.config(text=f"总大小: {format_size(root.size)}")
        self.count_label.config(text=f"文件/文件夹: {node_count}")
        self.depth_label.config(text=f"最大深度: {max_depth}")
        self.update_file_stats(root)

    def build_tree(self, root):
        self.tree.delete(*self.tree.get_children())
        self.item_to_node.clear()
        self.path_to_item.clear()
        self.item_to_path.clear()
        node_count = 0
        max_depth = 0

        def insert_node(parent_id, node, rel_path, depth):
            nonlocal node_count, max_depth
            node_count += 1
            max_depth = max(max_depth, depth)
            item_id = self.tree.insert(
                parent_id, 'end', text=self._node_display(node),
                values=(node.size, node.is_dir), tags=self._node_tags(node),
                open=depth < 3,
            )
            self.item_to_node[item_id] = node
            self.path_to_item[rel_path] = item_id
            self.item_to_path[item_id] = rel_path
            for child in node.children:
                insert_node(item_id, child, rel_path + (child.name,), depth + 1)
            return item_id

        insert_node('', root, (), 0)
        self.live_max_depth = max_depth
        self.total_label.config(text=f"总大小: {format_size(root.size)}")
        self.count_label.config(text=f"文件/文件夹: {node_count}")
        self.depth_label.config(text=f"最大深度: {max_depth}")
        self.update_file_stats(root)

    def update_file_stats(self, root):
        stats = {}
        def collect_stats(node):
            if not node.is_dir:
                ext = node.name.rsplit('.', 1)[-1].lower() if '.' in node.name else 'no_ext'
                if ext not in stats:
                    stats[ext] = {'count': 0, 'size': 0}
                stats[ext]['count'] += 1
                stats[ext]['size'] += node.size
            else:
                for child in node.children:
                    collect_stats(child)
        collect_stats(root)
        sorted_stats = sorted(stats.items(), key=lambda x: x[1]['size'], reverse=True)[:20]
        max_size = sorted_stats[0][1]['size'] if sorted_stats else 1
        text = ""
        for ext, data in sorted_stats:
            bar_len = int(data['size'] / max_size * 25)
            bar = "█" * bar_len + "░" * (25 - bar_len)
            text += f".{ext:<8} {bar}  {data['count']:>4}  {format_size(data['size'])}\n"
        self.stats_text.config(state='normal')
        self.stats_text.delete(1.0, 'end')
        self.stats_text.insert('end', text)
        self.stats_text.config(state='disabled')

    def show_context_menu(self, event):
        item = self.tree.identify_row(event.y)
        if item:
            self.tree.selection_set(item)
            self.context_menu.post(event.x_root, event.y_root)

    def get_selected_node(self):
        selection = self.tree.selection()
        if not selection:
            return None
        item_id = selection[0]
        return self.item_to_node.get(item_id)

    def sort_children_of_node(self, node, key_func, reverse=False):
        item_id = next((iid for iid, current in self.item_to_node.items() if current is node), None)
        if not item_id:
            return
        node.children.sort(key=key_func, reverse=reverse)
        base_path = self.item_to_path.get(item_id, ())
        for index, child in enumerate(node.children):
            child_id = self.path_to_item.get(base_path + (child.name,))
            if child_id:
                self.tree.move(child_id, item_id, index)

    def sort_by_size_desc(self):
        node = self.get_selected_node()
        if node:
            self.sort_children_of_node(node, lambda x: x.total_size(), True)

    def sort_by_size_asc(self):
        node = self.get_selected_node()
        if node:
            self.sort_children_of_node(node, lambda x: x.total_size(), False)

    def sort_by_name_az(self):
        node = self.get_selected_node()
        if node:
            self.sort_children_of_node(node, lambda x: x.name.lower(), False)

    def sort_by_name_za(self):
        node = self.get_selected_node()
        if node:
            self.sort_children_of_node(node, lambda x: x.name.lower(), True)

    def expand_all(self):
        def expand(item):
            self.tree.item(item, open=True)
            for child in self.tree.get_children(item):
                expand(child)
        for item in self.tree.get_children(''):
            expand(item)

    def collapse_all(self):
        for item in self.tree.get_children(''):
            self.tree.item(item, open=False)

    def copy_name(self):
        node = self.get_selected_node()
        if node:
            self.root.clipboard_clear()
            self.root.clipboard_append(node.name)

    def copy_path(self):
        selection = self.tree.selection()
        if not selection or not self.analyzed_path:
            return
        relative_path = self.item_to_path.get(selection[0])
        if relative_path is None:
            return
        full_path = Path(self.analyzed_path).joinpath(*relative_path)
        self.root.clipboard_clear()
        self.root.clipboard_append(str(full_path))

    def focus_search(self):
        self.search_entry.focus_set()
        self.search_entry.select_range(0, 'end')

    def apply_search(self):
        if not self.current_root:
            return
        query = self.search_entry.get().strip().lower()
        if not query:
            self.clear_search()
            return

        # Rebuild once so repeated searches also restore previously detached rows.
        self.build_tree(self.current_root)

        def filter_node(node, rel_path):
            matched = query in node.name.lower()
            for child in node.children:
                child_path = rel_path + (child.name,)
                child_matches = filter_node(child, child_path)
                child_id = self.path_to_item.get(child_path)
                if child_id and not child_matches:
                    self.tree.detach(child_id)
                matched = matched or child_matches
            item_id = self.path_to_item.get(rel_path)
            if matched and item_id:
                self.tree.item(item_id, open=True)
            return matched

        filter_node(self.current_root, ())

    def clear_search(self):
        if not self.current_root:
            return
        # Rebuild tree to show all items
        self.build_tree(self.current_root)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    app = NeonGUI()
    app.run()
