#!/usr/bin/env python3
"""
文件夹大小分析器 - 现代浅色风格 GUI (Treeview版本，修复滚动性能)
"""

import ctypes
import gc
import os
import queue
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import filedialog, ttk
from typing import Dict, List, Optional

FONT = 'Microsoft YaHei UI'
MONO = 'Consolas'

# 读不到东西时，把系统给的错误码翻译成人话
ERROR_LABELS = {
    5: '没权限（系统文件夹之类）',
    2: '扫描过程中被删掉了',
    32: '被别的程序占着',
    23: '磁盘这一块读不出来（坏道）',
    1392: '文件系统结构坏了',
    1393: '文件系统结构坏了',
}
ERROR_NAMES = {
    5: '拒绝访问',
    2: '文件已不存在',
    32: '文件被占用',
    23: '数据校验错',
    1392: '文件或目录损坏',
    1393: '磁盘结构损坏',
}
DAMAGE_CODES = {23, 1392, 1393}


class Palette:
    """浅色主题配色：白底、灰边框、单一蓝色强调色。"""
    app_bg = '#F3F4F6'        # 窗口底色
    surface = '#FFFFFF'       # 卡片 / 面板底色
    surface_alt = '#F9FAFB'   # 次级底色（表头）
    border = '#E5E7EB'        # 分隔线、边框
    text = '#111827'          # 主文字
    text_muted = '#6B7280'    # 次要文字
    text_soft = '#9CA3AF'     # 更淡的辅助文字
    accent = '#2563EB'        # 强调色（蓝）
    accent_dark = '#1D4ED8'   # 强调色按下
    accent_soft = '#EFF6FF'   # 强调色浅底（悬停）
    danger = '#DC2626'
    danger_soft = '#FEF2F2'
    selection = '#DBEAFE'     # 选中行底色


def enable_dpi_awareness() -> bool:
    """让 Windows 按真实分辨率渲染，文字不糊。"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
        return True
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
        return True
    except Exception:
        return False


def get_scale_factor() -> float:
    try:
        factor = ctypes.windll.shcore.GetScaleFactorForDevice(0) / 100.0
        if 0.5 <= factor <= 4.0:
            return factor
    except Exception:
        pass
    return 1.0


class FileNode:
    """一个节点。用 __slots__ 不用 dataclass —— 一次扫描要造三十万个，快很多。"""

    __slots__ = ('name', 'size', 'is_dir', 'children')

    def __init__(self, name: str, size: int, is_dir: bool,
                 children: Optional[List['FileNode']] = None):
        self.name = name
        self.size = size
        self.is_dir = is_dir
        self.children = children if children is not None else []

    def total_size(self) -> int:
        """Return the cached aggregate size (O(1) for directories)."""
        return self.size


def display_width(text: str) -> int:
    """中文算两个格子，英文算一个 —— 排版对齐用"""
    return sum(2 if ord(ch) > 0x2E80 else 1 for ch in text)


def pad_to(text: str, width: int) -> str:
    return text + ' ' * max(1, width - display_width(text))


def pad_left(text: str, width: int) -> str:
    """右对齐的补齐：中文占两格，所以也得按"几格"算，不能按字数算。"""
    return ' ' * max(0, width - display_width(text)) + text


def elide_middle(text: str, limit: int) -> str:
    """太长了就从中间掐掉，头和尾都留着。

    路径不能掐尾巴 —— 掐了尾巴就不知道是哪个文件了；也不能只留尾巴 —— 那就看不出在哪个盘。
    """
    if len(text) <= limit:
        return text
    keep = max(1, limit - 1)
    head = int(keep * 0.45)
    return text[:head] + '…' + text[len(text) - (keep - head):]


# ---------- 结构图的排版算法（纯计算，不碰界面，好单独测） ----------

def squarify(items, x, y, width, height):
    """方块图排版：把一块地方按大小比例分给一堆东西。

    items 是 [(节点, 大小)]，调用前按大小从大到小排好。
    返回 [(节点, x, y, 宽, 高), ...]。

    用的是方块图的老算法：一行一行贴着短边铺，铺到"再塞进去就变成细长条"就换下一行 ——
    细长条最看不清东西，所以宁可慢慢凑，也要让每块尽量接近正方形。
    """
    boxes = []
    total = sum(size for _node, size in items)
    if total <= 0 or width <= 0 or height <= 0 or not items:
        return boxes
    scale = (width * height) / float(total)          # 一个字节占多少面积
    rest = list(items)
    cx, cy, cw, ch = float(x), float(y), float(width), float(height)
    while rest:
        if cw >= ch:
            row, area, used = _take_row(rest, ch, scale)
            band = area / ch if ch else 0.0          # 这一条有多宽
            oy = cy
            for node, size in row:
                piece = (size * scale / band) if band else 0.0
                boxes.append((node, cx, oy, band, piece))
                oy += piece
            cx += band
            cw -= band
        else:
            row, area, used = _take_row(rest, cw, scale)
            band = area / cw if cw else 0.0          # 这一条有多高
            ox = cx
            for node, size in row:
                piece = (size * scale / band) if band else 0.0
                boxes.append((node, ox, cy, piece, band))
                ox += piece
            cy += band
            ch -= band
        del rest[:used]
    return boxes


def _take_row(items, side, scale):
    """从 items 头上凑一条：这条沿着 side 铺满，往里塞到"再塞就变细长条"为止。

    返回 (这条里有哪些, 这条占多少面积, 用掉了几个)。好歹会塞一个进去，不会卡死。
    """
    row = []
    area = 0.0
    best = None
    for index, (node, size) in enumerate(items):
        next_area = area + size * scale
        thickness = next_area / side if side else 0.0
        if thickness <= 0:
            break
        longest = max(s * scale / thickness for _n, s in items[:index + 1])
        shortest = min(thickness, longest)
        ratio = (max(thickness, longest) / shortest) if shortest > 0 else float('inf')
        if best is not None and ratio > best:
            break
        best = ratio
        area = next_area
        row.append((node, size))
    if not row:
        row = [items[0]]
        area = items[0][1] * scale
    return row, area, len(row)


def mindmap_rows(root, max_depth, min_size, row_height, collapsed=None):
    """思维导图排版：根节点钉在最上面，下面一层占一列、从上往下铺。

    返回 [(节点, 第几层, 中心 y), ...]，父在前、子在后，照着顺序画就行。
    地盘大的排前面（跟列表里一个顺序）—— 一打开图，最大的那几支先入眼。
    根节点不跟着孩子居中，就钉在顶上 —— 不然图一高，根就跑到屏幕外面去了。
    collapsed 里放着的节点当成"收起来了"，它下面的就不铺。
    """
    rows = []
    cursor = [row_height / 2.0]

    def walk(node, level):
        kids = []
        if level < max_depth and not (collapsed and id(node) in collapsed):
            kids = [child for child in node.children if child.size >= min_size]
        if kids:
            centres = [walk(child, level + 1) for child in kids]
            centre_y = (centres[0] + centres[-1]) / 2.0
        else:
            centre_y = cursor[0]
            cursor[0] += row_height
        if level == 0:
            centre_y = row_height / 2.0            # 根节点钉在最上面
        rows.append((node, level, centre_y))
        return centre_y

    walk(root, 0)
    rows.reverse()          # 父在前、子在后
    return rows


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
    PAUSE_CHECK_EVERY = 128      # 每扫这么多项才查一次"暂停/停止"，省下几十万次加锁
    CLOCK_CHECK_EVERY = 32       # 每攒这么多项才看一次表，省下几十万次取时间

    def __init__(self):
        self.running = False
        self.paused = False
        self.thread: Optional[threading.Thread] = None
        self.result: Optional[FileNode] = None
        self.files_scanned = 0
        self.directories_scanned = 0
        self.bytes_scanned = 0
        self.errors = 0
        self.error_kinds: Dict[int, int] = {}
        self.symlinks_skipped = 0
        self.max_depth = 0
        self.ext_stats: Dict[str, List[int]] = {}
        self.generation = 0
        self.events = queue.SimpleQueue()
        self._pause_event = threading.Event()
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._pending_entries = []
        self._pending_deltas = {}
        self._pending_counts = {}
        self._last_flush = 0.0
        self._entry_tick = 0

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
            self.error_kinds = {}
            self.symlinks_skipped = 0
            self.max_depth = 0
            self.ext_stats = {}
            self.events = queue.SimpleQueue()
            self._pending_entries = []
            self._pending_deltas = {}
            self._pending_counts = {}
            self._last_flush = time.monotonic()
            self._entry_tick = 0
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
        pending = self._pending_entries
        pending.append((parent_path, node_path, name, size, is_dir))
        # 顺手记一笔"这个文件夹里数到了几项"，界面拿它当占位行上的真数字
        counts = self._pending_counts
        counts[parent_path] = counts.get(parent_path, 0) + 1
        if not is_dir:
            self._pending_deltas[parent_path] = self._pending_deltas.get(parent_path, 0) + size
        count = len(pending)
        if count >= self.BATCH_ENTRY_LIMIT:
            self._flush_pending(generation)
        elif not count % self.CLOCK_CHECK_EVERY and time.monotonic() - self._last_flush >= self.BATCH_INTERVAL:
            self._flush_pending(generation)

    def _flush_pending(self, generation: int):
        if generation != self.generation or (not self._pending_entries and not self._pending_deltas):
            return
        event = {
            'type': 'batch',
            'generation': generation,
            'entries': self._pending_entries,
            'deltas': self._pending_deltas,
            'counts': self._pending_counts,
            'files': self.files_scanned,
            'directories': self.directories_scanned,
            'bytes': self.bytes_scanned,
            'errors': self.errors,
        }
        self._pending_entries = []
        self._pending_deltas = {}
        self._pending_counts = {}
        self._last_flush = time.monotonic()
        self.events.put(event)

    def _analyze(self, path: Path, generation: int):
        self.directories_scanned = 1
        # 扫描期间关掉垃圾回收：要造三十万个节点对象，边造边回收纯属浪费
        gc_was_on = gc.isenabled()
        gc.disable()
        try:
            root = self._scan_directory(path, (), generation)
            self._flush_pending(generation)
        finally:
            if gc_was_on:
                gc.enable()
        cancelled = not self._is_active(generation)
        if generation == self.generation:
            self.result = root
            # 先把结果放进队列，再清"在跑"的标记。
            # 反过来的话，界面可能在中间那一瞬间看到"没在跑、也没结果"，直接收工不干了。
            self.events.put({
                'type': 'complete',
                'generation': generation,
                'root': root,
                'cancelled': cancelled,
                'files': self.files_scanned,
                'directories': self.directories_scanned,
                'bytes': self.bytes_scanned,
                'errors': self.errors,
                'error_kinds': dict(self.error_kinds),
                'symlinks': self.symlinks_skipped,
                'depth': self.max_depth,
                'stats': self.ext_stats,
            })
            self.running = False
            self.paused = False

    def _scan_directory(self, path: Path, relative_path, generation: int) -> FileNode:
        node = FileNode(name=path.name or str(path), size=0, is_dir=True, children=[])
        depth = len(relative_path)
        if depth > self.max_depth:
            self.max_depth = depth
        try:
            with os.scandir(path) as entries:
                for entry in entries:
                    self._entry_tick += 1
                    if self._entry_tick >= self.PAUSE_CHECK_EVERY:
                        self._entry_tick = 0
                        if not self._wait_if_paused(generation):
                            break
                    try:
                        name = entry.name
                        if entry.is_symlink():
                            # 不跟随链接，否则可能绕圈。数量会显示在"未计入"里，不偷偷丢
                            self.symlinks_skipped += 1
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
                            # 顺手把"文件类型统计"攒出来，省得界面扫完再走一遍 27 万个文件
                            dot = name.rfind('.')
                            ext = name[dot + 1:].lower() if dot >= 0 else 'no_ext'
                            bucket = self.ext_stats.get(ext)
                            if bucket is None:
                                bucket = self.ext_stats[ext] = [0, 0]
                            bucket[0] += 1
                            bucket[1] += size
                    except OSError as exc:
                        self._record_error(exc)
        except OSError as exc:
            self._record_error(exc)

        node.children.sort(key=lambda child: child.size, reverse=True)
        return node

    def _record_error(self, exc):
        """记一笔读不到的东西，顺便按错误码分类 —— 好让界面说清楚是"没权限"还是"盘坏了"。"""
        self.errors += 1
        code = getattr(exc, 'winerror', None)
        if code is None:
            code = getattr(exc, 'errno', None) or 0
        self.error_kinds[code] = self.error_kinds.get(code, 0) + 1

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


def toggle_scrollbar(bar, needed: bool, before, side, fill, padx, pady):
    """滚动条该露脸就摆上、不该露就收起来。

    摆的时候插在 before 前头 —— 不然它排在列表后头，列表先把地方占光，它就挤不进去了。
    """
    if needed == bool(bar.winfo_manager()):
        return
    if needed:
        bar.pack(side=side, fill=fill, padx=padx, pady=pady, before=before)
    else:
        bar.pack_forget()


class HoverTip:
    """鼠标停一会儿才弹的小气泡（整套程序共用一个，不会留一堆残影）。"""

    def __init__(self, root, font, delay_ms=450, pad=(8, 4)):
        self.root = root
        self.font = font
        self.delay_ms = delay_ms
        self.pad = pad
        self._job = None
        self._window = None
        self._label = None

    def schedule(self, text):
        self.cancel()
        if not text:
            return
        self._job = self.root.after(self.delay_ms, lambda: self._pop(text))

    def cancel(self):
        if self._job is not None:
            self.root.after_cancel(self._job)
            self._job = None
        if self._window is not None and self._window.winfo_ismapped():
            self._window.withdraw()

    def _pop(self, text):
        self._job = None
        if self._window is None:
            self._window = tk.Toplevel(self.root)
            self._window.overrideredirect(True)
            self._window.attributes('-topmost', True)
            self._label = tk.Label(self._window, text=text, font=self.font,
                bg=Palette.text, fg='#FFFFFF', padx=self.pad[0], pady=self.pad[1],
                justify='left')
            self._label.pack()
        else:
            self._label.config(text=text)
        x, y = self.root.winfo_pointerxy()
        self._window.geometry(f"+{x + 14}+{y + 18}")
        self._window.deiconify()
        self._window.lift()


class CleanScrollbar(tk.Canvas):
    """自己画的滚动条。

    为啥不用 Tk 自带的：自带滚动条的滑块长度是按"内容比例"算的 ——
    列表里四万行、几十万行的时候，滑块只剩不到 1 个像素，看不见也抓不住。
    自己画就能给滑块保一个最小长度，几万行也照样看得见、抓得动。
    内容一屏装得下时由界面把它收起来（所以正常状态下列表边上不会有灰杠）。
    """

    MIN_THUMB = 24          # 滑块最短多少像素
    TRACK = '#DCE0E6'       # 滑槽
    THUMB = '#A9B2BD'       # 滑块
    THUMB_HOVER = '#808A97'  # 鼠标压上去时

    def __init__(self, master, orient='vertical', command=None, thickness=12, **kw):
        vertical = orient == 'vertical'
        super().__init__(master,
            width=thickness if vertical else 1,
            height=1 if vertical else thickness,
            highlightthickness=0, bd=0, bg=kw.pop('bg', Palette.surface), **kw)
        self.orient = orient
        self.command = command
        self.thickness = thickness
        self.first = 0.0
        self.last = 1.0
        self._grab_offset = None
        self._hover = False
        self.bind('<Configure>', lambda _event: self._redraw())
        self.bind('<Button-1>', self._on_press)
        self.bind('<B1-Motion>', self._on_drag)
        self.bind('<ButtonRelease-1>', lambda _event: setattr(self, '_grab_offset', None))
        self.bind('<Enter>', lambda _event: self._set_hover(True))
        self.bind('<Leave>', lambda _event: self._set_hover(False))
        self.bind('<MouseWheel>', self._on_wheel)

    # ---------- Tk 那边调用的接口 ----------

    def set(self, first, last):
        """Tk 每次滚动 / 内容变化都会调这里。first、last 是"看得见的区间占多少"。"""
        self.first, self.last = float(first), float(last)
        self._redraw()

    def is_scrollable(self) -> bool:
        """内容装不下才需要它露脸。"""
        return self.last - self.first < 1.0

    # ---------- 画 ----------

    def _length(self) -> int:
        return self.winfo_height() if self.orient == 'vertical' else self.winfo_width()

    def _thumb_span(self):
        """算出滑块该从哪儿画到哪儿（像素）。

        关键在最后那步换算：滑块被顶到最小长度后，它"能走的路"就短了，
        位置也必须按"能走的路"来摊，不然一抓就跳。
        """
        length = self._length()
        fraction = min(1.0, max(0.0, self.last - self.first))
        thumb = min(length, max(self.MIN_THUMB, fraction * length))
        travel = max(0.0, length - thumb)
        if fraction >= 1.0 or travel <= 0:
            start = 0.0
        else:
            start = min(travel, max(0.0, self.first / (1.0 - fraction) * travel))
        return start, thumb

    def _redraw(self):
        self.delete('all')
        length = self._length()
        thickness = self.thickness
        if length <= 2 or thickness <= 2:
            return
        vertical = self.orient == 'vertical'
        if vertical:
            self.create_rectangle(3, 0, thickness - 3, length, fill=self.TRACK, outline='')
        else:
            self.create_rectangle(0, 3, length, thickness - 3, fill=self.TRACK, outline='')
        start, thumb = self._thumb_span()
        if thumb >= length - 1:
            return                      # 一屏装得下，整条就是滑块，没必要再画
        color = self.THUMB_HOVER if self._hover else self.THUMB
        radius = (thickness - 4) / 2.0
        centre = thickness / 2.0
        if vertical:
            self.create_line(centre, start + radius, centre, start + thumb - radius,
                width=thickness - 4, capstyle='round', fill=color)
        else:
            self.create_line(start + radius, centre, start + thumb - radius, centre,
                width=thickness - 4, capstyle='round', fill=color)

    def _set_hover(self, hover: bool):
        self._hover = hover
        self._redraw()

    # ---------- 抓滑块 / 点滑槽 ----------

    def _pos(self, event) -> int:
        return event.y if self.orient == 'vertical' else event.x

    def _on_press(self, event):
        position = self._pos(event)
        start, thumb = self._thumb_span()
        if start <= position <= start + thumb:
            self._grab_offset = position - start      # 抓住滑块本身，别让它跳
        else:
            self._grab_offset = None
            if self.command is not None:              # 点滑槽 = 翻一页，跟系统滚动条一个脾气
                self.command('scroll', 1 if position > start else -1, 'pages')

    def _on_drag(self, event):
        if self._grab_offset is None or self.command is None:
            return
        length = self._length()
        start, thumb = self._thumb_span()
        travel = max(1.0, length - thumb)
        fraction = min(1.0, max(0.0, self.last - self.first))
        wanted = min(travel, max(0.0, self._pos(event) - self._grab_offset))
        self.command('moveto', wanted / travel * (1.0 - fraction))

    def _on_wheel(self, event):
        """鼠标压在滚动条上滚轮也得管用（不然那一条就成了死区）。"""
        if self.command is not None:
            self.command('scroll', -1 if event.delta > 0 else 1, 'units')


class StructureWindow:
    """结构图窗口：一座文件夹的"全景图"。

    两种看法：
      · 方块图 —— 一块地方 = 一个文件夹，面积就是它的大小，谁占地方一眼就看出来；
      · 思维导图 —— 一层占一列，看的是层级结构。
    两个旋钮随时改（改完立刻重画）：画到第几层、小于百分之几的不画。
    双击钻进去看它里面，右键或按钮退回上一层，鼠标停住看完整路径。
    """

    ROW_HEIGHT = 26         # 思维导图：一行多高（跟着缩放走）
    COLUMN_WIDTH = 232      # 思维导图：一层占多宽（跟着缩放走）
    MIN_BLOCK_PX = 3.0      # 方块小于这么多像素就不画了（画了也看不清）
    GAP = 2.0               # 两块之间留条缝 —— 有缝才看得出"这儿是两块"，不然糊成一片
    MIN_ZOOM = 0.35
    MAX_ZOOM = 2.4
    ZOOM_STEP = 1.12
    MIN_SHARE = 0.05        # 小于百分之几的不画：最低就是 0.05%，再往下就成一堆线了

    # 每一支一个色系、三个深浅（越浅离根越远）—— 一眼看出哪几块是"同一支"的。
    # 都是柔和浅色配深字：一屏大色块也不刺眼。
    FAMILIES = (
        ('#93B4E6', '#BED2F1', '#E4EDFA'),   # 蓝
        ('#8FCFC4', '#BCE2DB', '#E2F2EF'),   # 青
        ('#B9A9E8', '#D5CDF2', '#EDE9FA'),   # 紫
        ('#E8C77E', '#F1DCAE', '#F9F0DC'),   # 黄
        ('#E9A3AE', '#F1C6CD', '#FAE6E9'),   # 粉
        ('#93CEA9', '#BEE2CC', '#E2F1E8'),   # 绿
        ('#B4BCC9', '#D2D7DE', '#EBEDF1'),   # 灰
    )

    def __init__(self, gui):
        self.gui = gui
        self.scale = gui.scale
        self.stack = [gui.current_root]          # 钻进来的路径（最后一个就是现在看的）
        self.mode = tk.StringVar(value='treemap')
        self.depth = tk.IntVar(value=3)
        self.min_share = tk.DoubleVar(value=0.3)
        self.zoom = 1.0                          # 思维导图的缩放（滚轮改）
        self.collapsed = set()                   # 思维导图里被收起来的节点（存 id）
        self._zoom_job = None                    # 缩放攒批的定时器
        self._pending_anchor = None              # 缩放完要把哪块按回光标底下
        self._block_nodes = {}                   # 画布图元 -> 节点
        self._block_rects = {}                   # 节点 id -> 它那块的位置（高亮用）
        self._block_parents = {}                 # 节点 id -> 装着它的那块（画虚线用）
        self._highlight = None                   # 鼠标压住的那块的高亮框
        self._hover_node = None
        self._render_job = None
        self._fonts = {}
        self.tip = HoverTip(gui.root, (FONT, 9), pad=(self.px(8), self.px(4)))

        self.win = tk.Toplevel(gui.root)
        self.win.title("结构图")
        self.win.configure(bg=Palette.app_bg)
        self.win.geometry(f"{self.px(1080)}x{self.px(740)}")
        self.win.minsize(self.px(760), self.px(480))
        self.win.protocol('WM_DELETE_WINDOW', self.close)
        self._build()
        self.win.after(80, lambda: self._render(focus='top'))

    def px(self, value: float) -> int:
        return max(1, int(round(value * self.scale)))

    # ---------- 搭界面 ----------

    def _build(self):
        bar = tk.Frame(self.win, bg=Palette.surface, padx=self.px(14), pady=self.px(10))
        bar.pack(fill='x')

        self.mode_buttons = {
            'treemap': self.gui._button(bar, "", lambda: self.set_mode('treemap'), 'ghost'),
            'mindmap': self.gui._button(bar, "", lambda: self.set_mode('mindmap'), 'ghost'),
        }
        self.mode_buttons['treemap'].pack(side='left')
        self.mode_buttons['mindmap'].pack(side='left', padx=(self.px(4), 0))

        tk.Frame(bar, bg=Palette.border, width=1).pack(
            side='left', fill='y', padx=self.px(12), pady=self.px(2))

        tk.Label(bar, text="画到第", font=(FONT, 9), fg=Palette.text_muted,
                 bg=Palette.surface).pack(side='left')
        tk.Spinbox(bar, from_=1, to=8, width=2, textvariable=self.depth,
            command=lambda: self._render(focus='top'), font=(FONT, 9), justify='center',
            relief='flat', bg=Palette.surface_alt, fg=Palette.text,
            buttonbackground=Palette.surface_alt, highlightthickness=1,
            highlightbackground=Palette.border).pack(side='left', padx=self.px(4))
        tk.Label(bar, text="层", font=(FONT, 9), fg=Palette.text_muted,
                 bg=Palette.surface).pack(side='left')

        tk.Label(bar, text="小于", font=(FONT, 9), fg=Palette.text_muted,
                 bg=Palette.surface).pack(side='left', padx=(self.px(14), 0))
        tk.Spinbox(bar, from_=self.MIN_SHARE, to=50, increment=0.1, width=4, textvariable=self.min_share,
            command=lambda: self._render(focus='top'), font=(FONT, 9), justify='center',
            relief='flat', bg=Palette.surface_alt, fg=Palette.text,
            buttonbackground=Palette.surface_alt, highlightthickness=1,
            highlightbackground=Palette.border).pack(side='left', padx=self.px(4))
        tk.Label(bar, text="% 的不画", font=(FONT, 9), fg=Palette.text_muted,
                 bg=Palette.surface).pack(side='left')

        self.back_btn = self.gui._button(bar, "返回上一层", self.go_back, 'secondary')
        self.back_btn.pack(side='left', padx=(self.px(16), 0))

        self.where_label = tk.Label(bar, text="", font=(FONT, 9),
            fg=Palette.text_soft, bg=Palette.surface)
        self.where_label.pack(side='right')

        body = tk.Frame(self.win, bg=Palette.app_bg, padx=self.px(14), pady=self.px(12))
        body.pack(fill='both', expand=True)
        card = tk.Frame(body, bg=Palette.surface,
            highlightthickness=1, highlightbackground=Palette.border)
        card.pack(fill='both', expand=True)

        self.canvas = tk.Canvas(card, bg=Palette.surface, highlightthickness=0, bd=0)
        self.canvas.pack(fill='both', expand=True)
        self.canvas.bind('<Configure>', lambda _event: self._render_soon(focus=None))
        self.canvas.bind('<Motion>', self._on_motion)
        self.canvas.bind('<Leave>', self._on_leave)
        self.canvas.bind('<Button-1>', self._on_click)
        self.canvas.bind('<Double-Button-1>', self._on_double_click)
        self.canvas.bind('<MouseWheel>', self._on_wheel)
        self.canvas.bind('<Button-2>', self._pan_start)        # 中键按住 = 拖画布
        self.canvas.bind('<B2-Motion>', self._pan_move)
        self.canvas.bind('<Button-3>', self._on_right_press)   # 右键：方块图=退回，导图=拖画布
        self.canvas.bind('<B3-Motion>', self._pan_move)

        self.hint_label = tk.Label(self.win, text="", font=(FONT, 9),
            fg=Palette.text_soft, bg=Palette.app_bg,
            padx=self.px(18), pady=self.px(8))
        self.hint_label.pack(fill='x')

        self._refresh_mode_buttons()
        self._refresh_hint()

    def _refresh_hint(self):
        """底下那行提示分模式说 —— 两个模式的手势不一样，混着写谁也看不懂。"""
        if self.mode.get() == 'treemap':
            text = "双击一块 = 钻进去看它里面 · 右键 = 退回上一层 · 鼠标停住看完整路径"
        else:
            text = ("双击一块 = 钻进去 · 点左边小三角 = 收起／展开它下面 · "
                    "中键或右键按住拖动 = 移画布 · 滚轮 = 缩放")
        self.hint_label.config(text=text)

    def _refresh_mode_buttons(self):
        for name, label in (('treemap', "方块图"), ('mindmap', "思维导图")):
            mark = '◉ ' if self.mode.get() == name else '○ '
            self.mode_buttons[name].config(text=mark + label)

    def set_mode(self, mode):
        if mode != self.mode.get():
            self.mode.set(mode)
            self._refresh_mode_buttons()
            self._refresh_hint()
            self._render(focus='top')

    def go_back(self):
        if len(self.stack) > 1:
            self.stack.pop()
            self._render()

    # ---------- 现在看的是哪个文件夹 ----------

    def current(self):
        return self.stack[-1]

    def path_of(self, node) -> str:
        """这个节点在磁盘上的完整路径。

        从这块往回沿"装着它的文件夹"链一路走到当前根，把名字倒序串起来。
        以前只拼"钻进来的链 + 这块自己的名字"，深层的块中间几层全被吞了 ——
        备忘录深处的 build-script-build.exe 就被报成了直接躺在 codex 根上，
        资源管理器里照着找根本找不到。
        """
        chain = []
        walker = node
        while walker is not None and walker is not self.current():
            chain.append(walker.name)
            walker = self._block_parents.get(id(walker))
        chain.reverse()
        parts = [item.name for item in self.stack[1:]] + chain
        base = self.gui.analyzed_path or ''
        return str(Path(base).joinpath(*parts)) if parts else base

    def min_size(self) -> float:
        """小于这个字节数的就不画（旋钮填的是百分比）。

        最低压到 0.05%：再往下就是几千块细条，看不清也不好看。
        """
        try:
            share = float(self.min_share.get())
        except (tk.TclError, ValueError):
            share = self.MIN_SHARE
        share = min(50.0, max(self.MIN_SHARE, share))
        return self.current().size * share / 100.0

    def levels(self) -> int:
        try:
            return max(1, min(8, int(self.depth.get())))
        except (tk.TclError, ValueError):
            return 3

    # ---------- 画 ----------

    def _render_soon(self, focus='top'):
        if self._render_job is None:
            self._render_job = self.win.after(60, lambda: self._render(focus=focus))

    def _render(self, focus='center'):
        """重画整张图。

        focus='center'：画完把当前根节点摆到画面正中（钻进去、退回来就用这个）；
        focus='top'：把根节点摆在左上角，最大的那几支先入眼（开图、改旋钮用这个）；
        focus=None：画面一动不动（缩放、收放节点 —— 它们自己会把手底下的东西对回原位）。
        """
        self._render_job = None
        if not self.win.winfo_exists():
            return
        self.tip.cancel()
        self._hover_node = None
        self._highlight = None
        self._zoom_job = None
        self._pending_anchor = None
        self.canvas.delete('all')
        self._block_nodes.clear()
        self._block_rects.clear()
        self._block_parents.clear()
        node = self.current()
        self.where_label.config(text=elide_middle(self.path_of(node), 72))
        self.back_btn.config(state='normal' if len(self.stack) > 1 else 'disabled')
        if self.mode.get() == 'treemap':
            self._render_treemap(node)
        else:
            self._render_mindmap(node)
            if focus == 'center':
                # 导图把根节点摆在整张图正中，钻进去的时候先让人看见自己在哪
                self._center_on(node)
            elif focus == 'top':
                # 开图 / 改旋钮：根节点摆到左上角，最大的那几支先入眼
                self._show_from_top(node)

    def _center_on(self, node):
        """把某个方块挪到画面正中：钻进去的时候，先让人看见自己在哪。"""
        rect = self._block_rects.get(id(node))
        if rect is None:
            return
        x, y, width, height = rect
        self._scroll_to(x + width / 2.0 - self.canvas.winfo_width() / 2.0,
                        y + height / 2.0 - self.canvas.winfo_height() / 2.0)

    def _show_from_top(self, node):
        """把某个方块挪到画面左上角：开图的时候，最大的那几支先入眼。"""
        rect = self._block_rects.get(id(node))
        if rect is None:
            return
        x, y, _width, _height = rect
        self._scroll_to(x - self.px(40), y - self.px(80))

    def _canvas_size(self):
        return self.canvas.winfo_width(), self.canvas.winfo_height()

    def _render_treemap(self, node):
        width, height = self._canvas_size()
        if width < 40 or height < 40:
            self._render_soon()
            return
        boxes = squarify(self._children_to_draw(node), 0.0, 0.0, float(width), float(height))
        for index, (child, bx, by, bw, bh) in enumerate(boxes):
            self._block_parents[id(child)] = node
            self._draw_treemap_block(child, bx, by, bw, bh, 1, index % len(self.FAMILIES))
        self.canvas.configure(scrollregion=(0, 0, width, height))

    def _children_to_draw(self, node):
        """这个文件夹里"值得画"的孩子：太小的不画，画了也就是一条缝。"""
        limit = self.min_size()
        return [(child, child.size) for child in node.children if child.size >= limit]

    def _draw_treemap_block(self, node, x, y, width, height, level, family):
        """画一块，再把它里面接着分下去。

        给进来的 (x, y, width, height) 是"这块地方"，真画的方块往里缩一圈 ——
        缩出来的缝就是两块之间的白线：有缝才看得出这儿是两块，不然糊成一片。
        """
        gap = self.px(self.GAP) / 2.0
        x, y = x + gap, y + gap
        width, height = width - gap * 2, height - gap * 2
        if width < self.MIN_BLOCK_PX or height < self.MIN_BLOCK_PX:
            return
        fill = self._fill_color(node, level, family)
        rect = self.canvas.create_rectangle(x, y, x + width, y + height,
            fill=fill, outline=self._edge_color(node, fill), width=self.px(1))
        self._block_nodes[rect] = node
        self._block_rects[id(node)] = (x, y, width, height)
        self._draw_treemap_label(node, x, y, width, height, level)
        header = self._header_px(width, height, level)
        can_go_deeper = (node.is_dir and node.children
                         and level < self.levels() and height > header * 2)
        inner = []
        if can_go_deeper:
            inner = squarify(self._children_to_draw(node), x, y + header,
                             max(0.0, width), max(0.0, height - header))
            for child, bx, by, bw, bh in inner:
                self._block_parents[id(child)] = node
                self._draw_treemap_block(child, bx, by, bw, bh, level + 1, family)
        if node.is_dir and node.children and not inner:
            self._draw_void_hint(node, x, y, width, height)

    def _draw_void_hint(self, node, x, y, width, height):
        """里面的东西没画出来的块（层数到头了 / 都太小被筛了），角上标一句。

        不标的话就是一大块空白，看着跟坏了似的；点一下钻进去就能看到里面。
        """
        if width < self.px(90) or height < self.px(40):
            return
        text = f"里面还有 {len(node.children)} 项 · 点一下进去看"
        font = self._font(8)
        if font.measure(text) > width - self.px(14):
            text = f"还有 {len(node.children)} 项…"
        self.canvas.create_text(x + self.px(7), y + height - self.px(6),
            anchor='sw', text=text, font=font, fill=Palette.text_muted)

    def _edge_color(self, node, fill):
        """文件夹描一圈比自己深一点的边：盒子套盒子，谁装着谁一眼看出。

        文件不描 —— 光板没边的那块就是文件，跟文件夹当场分开。
        """
        if not node.is_dir:
            return ''
        r, g, b = int(fill[1:3], 16), int(fill[3:5], 16), int(fill[5:7], 16)
        return '#%02X%02X%02X' % (int(r * 0.72), int(g * 0.72), int(b * 0.72))

    def _label_roomy(self, width, height):
        """这块地方算不算宽敞：宽敞就多写点，窄就只写名字。"""
        return width >= self.px(150) and height >= self.px(46)

    def _header_px(self, width, height, level):
        """方块顶上得给字留多高的地方。

        以前写死 15 像素，可宽敞块要写两行字，两行得 40 多像素 ——
        多出来的字全压到下面的子方块上，名字糊成一团。
        字有多高直接问字体本人要，不写死，缩放、换电脑都不会错。
        头衔也跟着层级走：越往里头的块，顶上留的地方越小。
        """
        top = self.px(5)
        roomy = self._label_roomy(width, height)
        if level <= 1 and roomy:
            two_lines = (self._font(11, bold=True).metrics('linespace')
                         + self._font(9).metrics('linespace'))
            return top + two_lines + self.px(4)
        if level == 2 and roomy:
            return top + self._font(9, bold=True).metrics('linespace') + self.px(2)
        return top + self._font(8).metrics('linespace') + self.px(2)

    def _draw_treemap_label(self, node, x, y, width, height, level):
        """方块上写名儿。

        头衔跟着层级走：最上层大字标题加"多大"，第二层中号粗体，
        再往下小字 —— 字越大越靠外，谁包着谁看字就知道。
        横排放不下的窄高条，把字竖过来写（从下往上读）；矮墩小格子
        宽度够就把名字掐短了写。实在一点地方没有的才留白。
        """
        roomy = self._label_roomy(width, height)
        if level <= 1 and roomy:
            size, bold, with_info = 11, True, True
        elif level == 2 and roomy:
            size, bold, with_info = 9, True, False
        else:
            size, bold, with_info = 8, False, False
        font = self._font(size, bold=bold)
        line_h = self.px(5) + font.metrics('linespace')
        if width >= self.px(58) and height >= line_h + self.px(1):
            self.canvas.create_text(x + self.px(7), y + self.px(5), anchor='nw',
                text=self._fit(node.name, width - self.px(14), size, bold=bold),
                font=font, fill=Palette.text)
            if with_info:
                share = node.size / max(1, self.current().size) * 100.0
                self.canvas.create_text(x + self.px(7), y + line_h + self.px(2),
                    anchor='nw',
                    text=f"{format_size(node.size)}  ·  占这里 {share:.1f}%",
                    font=self._font(9), fill=Palette.text_muted)
            return
        if width >= self.px(16) and height >= self.px(58):
            # 窄高条：字一个一个正着摞下来（竖排招牌那种）。
            # 以前整行转 90 度躺着写，中文歪头看着别扭，摞着排才顺眼。
            room_chars = max(1, int((height - self.px(12))
                                    // self._font(8).metrics('linespace')))
            text = node.name
            if len(text) > room_chars:
                text = text[:max(1, room_chars - 1)] + '…'
            self.canvas.create_text(x + width / 2.0, y + self.px(6), anchor='n',
                justify='center', text='\n'.join(text),
                font=self._font(8), fill=Palette.text)
            return
        if width >= self.px(34) and height >= line_h + self.px(1):
            # 矮墩块：横排挤得下几个字就掐短了写几个
            self.canvas.create_text(x + self.px(4), y + self.px(4), anchor='nw',
                text=self._fit(node.name, width - self.px(8), 8),
                font=self._font(8), fill=Palette.text)

    def _render_mindmap(self, node):
        width, height = self._canvas_size()
        if width < 40 or height < 40:
            self._render_soon()
            return
        limit = self.min_size()
        zoom = self.zoom
        col = max(56.0, self.px(self.COLUMN_WIDTH) * zoom)      # 一层占多宽
        row = max(11.0, self.px(self.ROW_HEIGHT) * zoom)        # 一行多高
        box_w = max(30.0, col - self.px(26) * zoom)
        box_h = max(7.0, row - self.px(6) * zoom)
        rows = mindmap_rows(node, self.levels(), limit, row, self.collapsed)
        places = {id(item): (self.px(18) + level * col, cy) for item, level, cy in rows}
        families = {id(node): 0}
        counter = 0
        for item, level, _cy in rows:                    # 先定颜色：一支一个色，深的沿用父的
            for child in item.children:
                if id(child) not in places:
                    continue
                self._block_parents[id(child)] = item
                if level == 0:
                    families[id(child)] = counter % len(self.FAMILIES)
                    counter += 1
                else:
                    families[id(child)] = families.get(id(item), 0)
        for item, _level, _cy in rows:                   # 连线画在方块底下
            x, y = places[id(item)]
            for child in item.children:
                if id(child) not in places:
                    continue
                cx, cy = places[id(child)]
                mid = (x + box_w + cx) / 2.0
                thick = max(1, int(min(6.0, max(1.5, zoom * 2.2),
                                       (child.size / max(1, node.size)) * 40)))
                self.canvas.create_line(x + box_w, y, mid, y, mid, cy, cx, cy,
                    fill='#C7CDD6', width=thick, joinstyle='miter')
        text_size = max(7, int(8 * zoom))
        for item, level, _cy in rows:
            x, y = places[id(item)]
            top = y - box_h / 2.0
            rect = self.canvas.create_rectangle(x, top, x + box_w, y + box_h / 2.0,
                fill=self._fill_color(item, level, families.get(id(item), 0)),
                outline='', width=0)
            self._block_nodes[rect] = item
            self._block_rects[id(item)] = (x, top, box_w, box_h)
            if level < self.levels() and any(child.size >= limit for child in item.children):
                marker = '▸' if id(item) in self.collapsed else '▾'
                tag = f"toggle{id(item)}"
                self.canvas.create_text(x - max(6.0, self.px(9) * zoom), y, text=marker,
                    font=self._font(max(8, int(9 * zoom)), bold=True),
                    fill=Palette.text_muted, tags=(tag,))
                self.canvas.tag_bind(tag, '<Button-1>',
                    lambda _event, target=item: self.toggle_collapse(target))
            if zoom >= 0.55:                  # 缩得太小就别写字了，写了也是糊的
                label = ("📁 " if item.is_dir else "") + f"{item.name}  {format_size(item.size)}"
                self.canvas.create_text(x + self.px(7), y, anchor='w',
                    text=self._fit(label, box_w - self.px(14), text_size),
                    font=self._font(text_size), fill=Palette.text)
        # 画布的可活动范围往外多放一圈：拖起来有"画布很大"的感觉，不会被内容框死
        region = self.canvas.bbox('all') or (0, 0, width, height)
        pad = self.px(500)
        x0, y0, x1, y1 = region
        self.canvas.configure(scrollregion=(x0 - pad, y0 - pad, x1 + pad, y1 + pad))

    def toggle_collapse(self, node):
        """收起／展开一个节点的子元素（导图里点它左边那个小三角）。

        收放完把这一块按回原来的屏幕位置 —— 不然底下一折，画面整体往上一跳，看着像闪了一下。
        """
        rect = self._block_rects.get(id(node))
        screen = None
        if rect is not None:
            screen = (rect[0] - self.canvas.canvasx(0), rect[1] - self.canvas.canvasy(0))
        key = id(node)
        if key in self.collapsed:
            self.collapsed.discard(key)
        else:
            self.collapsed.add(key)
        self._render(focus=None)
        rect = self._block_rects.get(id(node))
        if screen is not None and rect is not None:
            self._scroll_to(rect[0] - screen[0], rect[1] - screen[1])

    # ---------- 缩放 / 拖画布 ----------

    def _pan_start(self, event):
        self.canvas.scan_mark(event.x, event.y)

    def _pan_move(self, event):
        self.canvas.scan_dragto(event.x, event.y, gain=1)

    def _on_right_press(self, event):
        """右键：方块图里是"退回上一层"，思维导图里是"按住拖画布"。"""
        if self.mode.get() == 'treemap':
            self.go_back()
        else:
            self._pan_start(event)

    def _on_wheel(self, event):
        """滚轮缩放。

        缩放时盯着光标底下那一块：缩完把这块按回光标底下，画面就不会乱跑。
        连着滚的时候攒一攒、统一重画一次 —— 一格一格立刻重画太卡。
        """
        if self.mode.get() != 'mindmap':
            return                        # 方块图本来就铺满一屏，没什么可缩的
        factor = self.ZOOM_STEP if event.delta > 0 else 1.0 / self.ZOOM_STEP
        new_zoom = min(self.MAX_ZOOM, max(self.MIN_ZOOM, self.zoom * factor))
        if abs(new_zoom - self.zoom) < 1e-6:
            return
        self._pending_anchor = self._capture_anchor(event)
        self.zoom = new_zoom
        if self._zoom_job is None:
            self._zoom_job = self.win.after(30, self._apply_zoom)

    def _capture_anchor(self, event):
        """记住光标底下（或附近）是哪一块、压在这块里的什么位置 —— 缩放完按这个对回去。

        光标正好压在块上最好；落在空白里就就近抓一块当"锚"，不然缩放完画面不知道往哪儿对。
        """
        node = self._node_at(event.x, event.y)
        rect = self._block_rects.get(id(node)) if node is not None else None
        if rect is None:
            node, rect = self._nearest_block(event.x, event.y)
        if rect is None or node is None:
            return None
        content_x = self.canvas.canvasx(event.x)
        content_y = self.canvas.canvasy(event.y)
        return (node,
                (content_x - rect[0]) / max(1.0, rect[2]),
                (content_y - rect[1]) / max(1.0, rect[3]),
                event.x, event.y)

    def _nearest_block(self, x, y):
        """离某个点最近的一块（带它的地盘）。找不到就返回 (None, None)。"""
        cx, cy = self.canvas.canvasx(x), self.canvas.canvasy(y)
        best_node = None
        best_rect = None
        best_dist = None
        for item_id, node in self._block_nodes.items():
            rect = self._block_rects.get(id(node))
            if rect is None:
                continue
            bx, by, bw, bh = rect
            dx = max(bx - cx, 0.0, cx - (bx + bw))
            dy = max(by - cy, 0.0, cy - (by + bh))
            dist = dx * dx + dy * dy
            if best_dist is None or dist < best_dist:
                best_dist = dist
                best_node = node
                best_rect = rect
        return best_node, best_rect

    def _apply_zoom(self):
        self._zoom_job = None
        anchor = self._pending_anchor
        self._pending_anchor = None
        self._render(focus=None)
        if anchor is not None:
            node, rel_x, rel_y, win_x, win_y = anchor
            rect = self._block_rects.get(id(node))
            if rect is not None:
                self._scroll_to(rect[0] + rel_x * rect[2] - win_x,
                                rect[1] + rel_y * rect[3] - win_y)

    def _scroll_to(self, x, y):
        """把画布挪到某个位置（用比例挪，画布就这么干的）。"""
        try:
            x0, y0, x1, y1 = (float(v) for v in str(self.canvas.cget('scrollregion')).split())
        except ValueError:
            return
        total_x = max(1.0, x1 - x0)
        total_y = max(1.0, y1 - y0)
        self.canvas.xview_moveto(min(1.0, max(0.0, (x - x0) / total_x)))
        self.canvas.yview_moveto(min(1.0, max(0.0, (y - y0) / total_y)))

    def _shade(self, level, family):
        tones = self.FAMILIES[family % len(self.FAMILIES)]
        return tones[0] if level <= 1 else tones[min(level - 1, 2)]

    def _fill_color(self, node, level, family):
        """文件永远用这一支最浅的那档色 —— 免得看着像"隔壁支"的。"""
        if not node.is_dir:
            return self.FAMILIES[family % len(self.FAMILIES)][2]
        return self._shade(level, family)

    def _font(self, size, bold=False):
        key = (int(size), bool(bold))
        font = self._fonts.get(key)
        if font is None:
            font = tkfont.Font(family=FONT, size=int(size),
                               weight='bold' if bold else 'normal')
            self._fonts[key] = font
        return font

    def _fit(self, text, max_px, size=8, bold=False):
        """太长就掐成"xxxx…"：按像素算的，不是按字数。"""
        if max_px <= 0:
            return ''
        font = self._font(size, bold)
        if font.measure(text) <= max_px:
            return text
        out = text
        while out and font.measure(out + '…') > max_px:
            out = out[:-1]
        return out + '…'

    # ---------- 鼠标 ----------

    def _node_at(self, x, y):
        """鼠标底下是哪一块（后画的压在上面，所以倒着找）。

        注意：传进来的是"窗口坐标"，得先换算成"画布坐标"再找 ——
        画布一拖动、一缩放，两边就对不上了，不换算就会指错块。
        """
        cx, cy = self.canvas.canvasx(x), self.canvas.canvasy(y)
        for item_id in reversed(self.canvas.find_overlapping(cx, cy, cx, cy)):
            node = self._block_nodes.get(item_id)
            if node is not None:
                return node
        return None

    def _on_motion(self, event):
        node = self._node_at(event.x, event.y)
        if node is self._hover_node:
            return
        self._hover_node = node
        self.tip.cancel()
        self._highlight_block(node)
        if node is None:
            return
        share = node.size / max(1, self.current().size) * 100.0
        self.tip.schedule(f"{elide_middle(self.path_of(node), 80)}\n"
                          f"{format_size(node.size)}  ·  占这里 {share:.1f}%")

    def _highlight_block(self, node):
        """鼠标压住哪块：粗蓝实线框它自己，细蓝虚线框"装着它的文件夹"。

        有虚线圈着 = 这块在人家里面；一圈虚线都没有 = 它就是最外层的，
        别被挤在大块角落缝里的小方块骗了 —— 那是邻居，不是成员。
        """
        if self._highlight is not None:
            self.canvas.delete(self._highlight)
            self._highlight = None
        self.canvas.delete('hover_chain')
        rect = self._block_rects.get(id(node)) if node is not None else None
        if rect is None:
            return
        x, y, width, height = rect
        self._highlight = self.canvas.create_rectangle(
            x - 1, y - 1, x + width + 1, y + height + 1,
            outline=Palette.accent, width=2)
        parent = self._block_parents.get(id(node))
        while parent is not None:
            prect = self._block_rects.get(id(parent))
            if prect is None:
                break
            self.canvas.create_rectangle(
                prect[0] - 1, prect[1] - 1,
                prect[0] + prect[2] + 1, prect[1] + prect[3] + 1,
                outline=Palette.accent, width=1, dash=(3, 3), tags='hover_chain')
            parent = self._block_parents.get(id(parent))

    def _on_leave(self, _event):
        self.tip.cancel()
        self._hover_node = None
        self._highlight_block(None)

    def _on_click(self, event):
        """单击：什么都不干。

        以前单击就钻进去 —— 最大化窗口后随手点一下画布，就误闯进深层了；
        双击还会被当成点了两次、连钻两层。现在钻进去只认双击。
        """
        pass

    def _on_double_click(self, event):
        """双击：钻进鼠标底下那块（方块图、思维导图都一样）。"""
        self._drill(self._node_at(event.x, event.y))

    def _drill(self, node):
        if node is None or not node.is_dir or not node.children:
            return
        self.stack.append(node)
        self._render()

    # ---------- 关窗 ----------

    def close(self):
        self.tip.cancel()
        if self._render_job is not None:
            self.win.after_cancel(self._render_job)
            self._render_job = None
        self.gui.structure_window = None
        self.win.destroy()


class FolderSizeGUI:
    SLICE_SECONDS = 0.008        # 每次轮询最多占用主线程 8 毫秒，超了就下一轮接着干
    MAX_LABEL_REFRESH = 200      # 每轮最多刷新 200 个节点的文字，防止父节点太多把主线程拖住
    STATS_TOP = 19               # 右侧"文件类型统计"最多列这么多种（加上表头刚好一屏）
    STATS_NAME_WIDTH = 9         # 那一栏里文件类型名占几格

    def __init__(self):
        self.dpi_ok = enable_dpi_awareness()
        self.scale = get_scale_factor() if self.dpi_ok else 1.0

        self.root = tk.Tk()
        self.root.title("文件夹大小分析器")
        if self.dpi_ok:
            self.root.tk.call('tk', 'scaling', self.scale * 96.0 / 72.0)
        self.root.configure(bg=Palette.app_bg)
        self.root.geometry(f"{self.px(1180)}x{self.px(780)}")
        self.root.minsize(self.px(900), self.px(560))
        self._center_window()

        self.row_height = self.px(28)

        self.analysis = AnalysisThread()
        self.current_root = None
        self.analyzed_path = None
        self.total_bytes = 0
        self.item_to_node: Dict[str, FileNode] = {}
        self.path_to_item = {}
        self.item_to_path = {}
        self.scan_generation = 0
        self._poll_job = None
        self.live_max_depth = 0
        self._queue_entries = []      # 等着往树里塞的节点
        self._queue_deltas = {}       # 等着加到父节点上的大小
        self._last_stats = None       # 最新一批统计数字，给标题栏用
        self._pending_complete = None # 扫描线程已干完，等界面把队排空再收尾
        self._stopping = False
        self._finished = True         # 本轮收尾（摆完结果、放开按钮）做完了吗
        self._deferred = {}           # 父文件夹没展开的条目先存这儿，等展开再摆
        self._open_paths = {()}       # 哪些文件夹是展开的（() 就是根）
        self._node_override = {}      # 点开文件夹时，把真正的节点对象带给摆行的那段代码
        self._open_on_insert = set()  # 这些路径的行摆上时要直接展开（搜索、展开全部用）
        self._placeholder_of = {}     # 文件夹路径 -> 它下面垫的那条占位行（为了画出展开箭头）
        self._child_counts = {}       # 文件夹路径 -> 里面已经数到几项（占位行上就写这个真数）
        self._last_click_item = ''
        self._scanning = False        # 是不是在扫（决定要不要刷标题栏那排数字）
        self._hover_item = ''         # 鼠标现在压在哪一行（悬停提示用）
        self.tip = HoverTip(self.root, (FONT, 9))
        self.structure_window = None  # 结构图窗口（开着就别再开第二个）
        self._pre_search_open = None  # 搜索前的展开状态，清空搜索时恢复用
        self._after_queue_label = None  # 队里这些行摆完之后，状态栏显示什么

        self.setup_styles()
        self.create_widgets()
        self.create_context_menu()

    # ---------- 尺寸 / 样式基础 ----------

    def px(self, value: int) -> int:
        return max(1, int(round(value * self.scale)))

    def _center_window(self):
        self.root.update_idletasks()
        width = self.px(1180)
        height = self.px(780)
        x = max(0, (self.root.winfo_screenwidth() - width) // 2)
        y = max(0, (self.root.winfo_screenheight() - height) // 3)
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    def setup_styles(self):
        style = ttk.Style()
        style.theme_use('clam')

        style.configure('Clean.Treeview',
            background=Palette.surface,
            fieldbackground=Palette.surface,
            foreground=Palette.text,
            borderwidth=0,
            relief='flat',
            rowheight=self.row_height,
            font=(FONT, 10),
        )
        style.layout('Clean.Treeview', [('Treeview.treearea', {'sticky': 'nswe'})])
        style.map('Clean.Treeview',
            background=[('selected', Palette.selection)],
            foreground=[('selected', Palette.text)],
        )

        style.configure('Clean.Treeview.Heading',
            background=Palette.surface_alt,
            foreground=Palette.text_muted,
            relief='flat',
            borderwidth=0,
            padding=(self.px(8), self.px(7)),
            font=(FONT, 9),
        )
        style.map('Clean.Treeview.Heading',
            background=[('active', Palette.surface_alt)],
            relief=[('active', 'flat')],
        )

        style.configure('Clean.Horizontal.TProgressbar',
            background=Palette.accent,
            troughcolor=Palette.border,
            bordercolor=Palette.border,
            darkcolor=Palette.accent,
            lightcolor=Palette.accent,
            borderwidth=0,
            thickness=self.px(4),
        )

    def _button(self, parent, text, command, kind='secondary'):
        specs = {
            'primary': (Palette.accent, '#FFFFFF', Palette.accent_dark, 0),
            'secondary': (Palette.surface, Palette.text, Palette.surface_alt, 1),
            'danger': (Palette.surface, Palette.danger, Palette.danger_soft, 1),
            'ghost': (Palette.surface, Palette.accent, Palette.accent_soft, 0),
        }
        bg, fg, hover, border = specs[kind]
        btn = tk.Button(parent, text=text, command=command,
            bg=bg, fg=fg,
            activebackground=hover, activeforeground=fg,
            disabledforeground=Palette.text_muted,
            font=(FONT, 10, 'bold' if kind == 'primary' else 'normal'),
            relief='flat', bd=0,
            highlightthickness=border,
            highlightbackground=Palette.border,
            highlightcolor=Palette.border,
            padx=self.px(16 if kind == 'primary' else 12),
            pady=self.px(7 if kind == 'primary' else 6),
            cursor='hand2',
        )

        def on_enter(_event):
            if str(btn['state']) != 'disabled':
                btn.configure(bg=hover)

        def on_leave(_event):
            if str(btn['state']) != 'disabled':
                btn.configure(bg=bg)

        btn.bind('<Enter>', on_enter)
        btn.bind('<Leave>', on_leave)
        return btn

    def _hline(self, parent, top=False):
        line = tk.Frame(parent, bg=Palette.border, height=1)
        if top:
            line.pack(fill='x', side='top')
        else:
            line.pack(fill='x')
        return line

    # ---------- 界面搭建 ----------

    def create_widgets(self):
        self._build_header()
        self._hline(self.root)
        self._build_toolbar()
        self._hline(self.root)
        self._build_content()
        self._hline(self.root, top=True)
        self._build_statusbar()

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

    def _build_header(self):
        header = tk.Frame(self.root, bg=Palette.surface,
                          padx=self.px(20), pady=self.px(14))
        header.pack(fill='x')

        tk.Label(header, text="文件夹大小分析器",
            font=(FONT, 15, 'bold'),
            fg=Palette.text, bg=Palette.surface).pack(side='left')

        stats = tk.Frame(header, bg=Palette.surface)
        stats.pack(side='right')
        # 从右往左 pack，最终显示顺序：总大小 / 项目数 / 最大深度
        self.depth_label = self._stat(stats, "最大深度", Palette.text)
        self.count_label = self._stat(stats, "项目数", Palette.text)
        self.total_label = self._stat(stats, "总大小", Palette.accent)

    def _stat(self, parent, title, value_color):
        box = tk.Frame(parent, bg=Palette.surface)
        box.pack(side='right', padx=(self.px(22), 0))
        tk.Label(box, text=title, font=(FONT, 9),
            fg=Palette.text_muted, bg=Palette.surface).pack(anchor='e')
        value = tk.Label(box, text="--", font=(FONT, 12, 'bold'),
            fg=value_color, bg=Palette.surface)
        value.pack(anchor='e')
        return value

    def _build_toolbar(self):
        bar = tk.Frame(self.root, bg=Palette.surface,
                       padx=self.px(20), pady=self.px(10))
        bar.pack(fill='x')

        self.browse_btn = self._button(bar, "选择文件夹", self.browse_folder, 'primary')
        self.browse_btn.pack(side='left')
        self.pause_btn = self._button(bar, "暂停", self.toggle_pause, 'secondary')
        self.pause_btn.pack(side='left', padx=(self.px(8), 0))
        self.stop_btn = self._button(bar, "停止", self.stop_analysis, 'danger')
        self.stop_btn.pack(side='left', padx=(self.px(8), 0))
        self.pause_btn.config(state='disabled')
        self.stop_btn.config(state='disabled')

        tk.Frame(bar, bg=Palette.border, width=1).pack(
            side='left', fill='y', padx=self.px(14), pady=self.px(2))

        self.expand_btn = self._button(bar, "展开全部", self.expand_all, 'ghost')
        self.expand_btn.pack(side='left')
        self.collapse_btn = self._button(bar, "收起全部", self.collapse_all, 'ghost')
        self.collapse_btn.pack(side='left', padx=(self.px(4), 0))
        self.structure_btn = self._button(bar, "结构图", self.open_structure, 'ghost')
        self.structure_btn.pack(side='left', padx=(self.px(12), 0))

        search = tk.Frame(bar, bg=Palette.surface)
        search.pack(side='right')
        self.clear_btn = self._button(search, "清除", self.clear_search, 'ghost')
        self.clear_btn.pack(side='right', padx=(self.px(6), 0))

        entry_box = tk.Frame(search, bg=Palette.surface,
            highlightthickness=1, highlightbackground=Palette.border,
            highlightcolor=Palette.border)
        entry_box.pack(side='right')
        tk.Label(entry_box, text="搜索", font=(FONT, 9),
            fg=Palette.text_muted, bg=Palette.surface).pack(
            side='left', padx=(self.px(8), self.px(4)), pady=self.px(5))
        self.search_entry = tk.Entry(entry_box,
            bg=Palette.surface, fg=Palette.text,
            disabledbackground=Palette.surface,
            font=(FONT, 10), width=18,
            insertbackground=Palette.text,
            relief='flat', bd=0,
            highlightthickness=0)
        self.search_entry.pack(side='left', padx=(0, self.px(8)), pady=self.px(5))
        self.search_entry.bind('<KeyRelease>', lambda e: self.apply_search())

    def _build_content(self):
        content = tk.Frame(self.root, bg=Palette.app_bg,
                           padx=self.px(16), pady=self.px(14))
        content.pack(fill='both', expand=True)

        # 右侧文件类型统计
        stats_card = tk.Frame(content, bg=Palette.surface, width=self.px(330),
            highlightthickness=1, highlightbackground=Palette.border)
        stats_card.pack(side='right', fill='y', padx=(self.px(12), 0))
        stats_card.pack_propagate(False)

        stats_head = tk.Frame(stats_card, bg=Palette.surface)
        stats_head.pack(fill='x', padx=self.px(14), pady=(self.px(12), self.px(6)))
        tk.Label(stats_head, text="文件类型统计", font=(FONT, 11, 'bold'),
            fg=Palette.text, bg=Palette.surface).pack(side='left')
        # 这行小字写"列了几类、一共几类" —— 不然把下面的数字加起来对不上总数，又得犯嘀咕
        self.stats_hint = tk.Label(stats_head, text="扫描完成后显示", font=(FONT, 9),
            fg=Palette.text_muted, bg=Palette.surface)
        self.stats_hint.pack(side='right')

        tk.Frame(stats_card, bg=Palette.border, height=1).pack(fill='x')

        self.stats_text = tk.Text(stats_card, height=10,
            bg=Palette.surface, fg=Palette.text,
            font=(MONO, 9),
            relief='flat', bd=0, highlightthickness=0,
            selectbackground=Palette.selection,
            state='disabled', wrap='none', cursor='arrow')
        self.stats_text.pack(fill='both', expand=True,
            padx=self.px(14), pady=self.px(10))
        self.stats_text.tag_configure('name', foreground=Palette.text)
        self.stats_text.tag_configure('head', foreground=Palette.text_soft)
        self.stats_text.tag_configure('accent', foreground=Palette.accent)
        self.stats_text.tag_configure('num', foreground=Palette.text_muted)
        self.stats_text.tag_configure('dim', foreground=Palette.text_soft)

        # 面板下半截：没算进去的东西（正常情况下是"无"）
        tk.Frame(stats_card, bg=Palette.border, height=1).pack(fill='x')

        uncounted_head = tk.Frame(stats_card, bg=Palette.surface)
        uncounted_head.pack(fill='x', padx=self.px(14), pady=(self.px(10), self.px(2)))
        tk.Label(uncounted_head, text="未计入", font=(FONT, 11, 'bold'),
            fg=Palette.text, bg=Palette.surface).pack(side='left')
        tk.Label(uncounted_head, text="除下列项外，全部算进去了", font=(FONT, 9),
            fg=Palette.text_muted, bg=Palette.surface).pack(side='right')

        self.uncounted_text = tk.Text(stats_card, height=5,
            bg=Palette.surface, fg=Palette.text,
            font=(MONO, 9),
            relief='flat', bd=0, highlightthickness=0,
            selectbackground=Palette.selection,
            state='disabled', wrap='none', cursor='arrow')
        self.uncounted_text.pack(fill='x', padx=self.px(14), pady=(0, self.px(10)))
        self.uncounted_text.tag_configure('name', foreground=Palette.text)
        self.uncounted_text.tag_configure('num', foreground=Palette.text_muted)
        self.uncounted_text.tag_configure('dim', foreground=Palette.text_soft)
        self.uncounted_text.tag_configure('warn', foreground=Palette.danger)

        # 左侧目录树
        tree_card = tk.Frame(content, bg=Palette.surface,
            highlightthickness=1, highlightbackground=Palette.border)
        tree_card.pack(side='left', fill='both', expand=True)

        # 两条滚动条是"自己画的"，平时藏着 —— 一屏装得下就没必要杵着一条灰杠
        self.scroll_y = CleanScrollbar(tree_card, orient='vertical')
        self.scroll_x = CleanScrollbar(tree_card, orient='horizontal')

        self.tree = ttk.Treeview(tree_card,
            style='Clean.Treeview',
            columns=('size', 'pct'),
            show='tree headings',
            yscrollcommand=self._on_tree_yscroll,
            xscrollcommand=self._on_tree_xscroll)
        self.tree.pack(side='left', fill='both', expand=True,
            padx=self.px(2), pady=self.px(2))
        self.scroll_y.command = self.tree.yview
        self.scroll_x.command = self.tree.xview

        self.tree.heading('#0', text="名称", anchor='w')
        self.tree.heading('size', text="大小", anchor='e')
        self.tree.heading('pct', text="占比", anchor='e')
        self.tree.column('#0', width=self.px(480), minwidth=self.px(220), stretch=True, anchor='w')
        self.tree.column('size', width=self.px(105), minwidth=self.px(80), stretch=False, anchor='e')
        self.tree.column('pct', width=self.px(70), minwidth=self.px(60), stretch=False, anchor='e')

        self.tree.tag_configure('dir', foreground=Palette.text, font=(FONT, 10, 'bold'))
        self.tree.tag_configure('file', foreground=Palette.text_muted, font=(FONT, 10))
        self.tree.tag_configure('placeholder', foreground=Palette.text_soft, font=(FONT, 10))

        self.tree.bind("<Button-3>", self.show_context_menu)
        self.tree.bind("<Button-1>", self.on_tree_click)
        self.tree.bind("<ButtonRelease-1>", self.after_tree_click)
        self.tree.bind("<<TreeviewSelect>>", self.on_tree_select)
        self.tree.bind("<Motion>", self.on_tree_motion)
        self.tree.bind("<Leave>", lambda _event: self.tip.cancel())
        self.tree.bind("<<TreeviewOpen>>", self.on_tree_open)
        self.tree.bind("<<TreeviewClose>>", self.on_tree_close)

    # ---------- "这行是谁家的"：状态栏 + 悬停提示 ----------

    def on_tree_select(self, event):
        """选中哪一行，状态栏就写它的完整路径 —— 层级再深也知道自己在哪、在谁的里面。"""
        selection = self.tree.selection()
        relative = self.item_to_path.get(selection[0]) if selection else None
        self.show_full_path(relative)

    def show_full_path(self, relative_path):
        if not self.analyzed_path:
            self.path_label.config(text="")
            return
        if relative_path is None:
            self.path_label.config(text=elide_middle(self.analyzed_path, 78))
            return
        full = str(Path(self.analyzed_path).joinpath(*relative_path))
        self.path_label.config(text=elide_middle(full, 78))

    def on_tree_motion(self, event):
        """鼠标在列表上滑过：停够半秒，就把这一行的完整路径弹出来（不用点）。"""
        item_id = self.tree.identify_row(event.y)
        if item_id == self._hover_item:
            return
        self._hover_item = item_id
        self.tip.cancel()
        if item_id:
            self.tip.schedule(elide_middle(self.full_path_text(item_id), 96))

    def full_path_text(self, item_id) -> str:
        """某一行对应的完整路径（拿不到就返回空串）。"""
        relative = self.item_to_path.get(item_id)
        if relative is None or not self.analyzed_path:
            return ""
        return str(Path(self.analyzed_path).joinpath(*relative))

    # ---------- 滚动条：要才露脸 ----------

    def _on_tree_yscroll(self, first, last):
        """Tk 每滚一下、内容一变就会调这里。顺手判断竖直滚动条该不该露脸。"""
        self.scroll_y.set(first, last)
        self.tip.cancel()
        self._toggle_scrollbar(self.scroll_y, self.scroll_y.is_scrollable(),
                               side='right', fill='y',
                               padx=(0, self.px(2)), pady=self.px(2))

    def _on_tree_xscroll(self, first, last):
        """横向同理。列表一般装得下，所以这条平时根本不出现。"""
        self.scroll_x.set(first, last)
        self._toggle_scrollbar(self.scroll_x, self.scroll_x.is_scrollable(),
                               side='bottom', fill='x',
                               padx=self.px(2), pady=(0, self.px(2)))

    def _toggle_scrollbar(self, bar, needed: bool, side, fill, padx, pady):
        """该露脸就摆上，不该露就收起来（一屏装得下的时候没必要杵着一条灰杠）。"""
        toggle_scrollbar(bar, needed, before=self.tree, side=side, fill=fill,
                         padx=padx, pady=pady)

    def _build_statusbar(self):
        bar = tk.Frame(self.root, bg=Palette.surface,
                       padx=self.px(20), pady=self.px(7))
        bar.pack(fill='x')

        self.progress = ttk.Progressbar(bar, mode='indeterminate',
            length=self.px(140), style='Clean.Horizontal.TProgressbar')

        self.scan_label = tk.Label(bar, text="选一个文件夹开始",
            font=(FONT, 9), fg=Palette.text_muted, bg=Palette.surface)
        self.scan_label.pack(side='left')

        self.path_label = tk.Label(bar, text="",
            font=(FONT, 9), fg=Palette.text_soft, bg=Palette.surface)
        self.path_label.pack(side='left', padx=(self.px(12), 0))

    def create_context_menu(self):
        self.context_menu = tk.Menu(self.tree, tearoff=0,
            bg=Palette.surface, fg=Palette.text,
            activebackground=Palette.selection, activeforeground=Palette.text,
            bd=1, relief='solid',
            font=(FONT, 10))
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

    # ---------- 扫描流程 ----------

    def browse_folder(self):
        folder = filedialog.askdirectory(title="选择要分析的文件夹")
        if folder:
            self.start_analysis(folder)

    def start_analysis(self, path):
        if self.structure_window is not None:
            self.structure_window.close()      # 重扫了，旧结构图作废
        self.analyzed_path = path
        self.current_root = None
        self.total_bytes = 0
        self.tree.delete(*self.tree.get_children())
        self.item_to_node.clear()
        self.path_to_item.clear()
        self.item_to_path.clear()
        self.live_max_depth = 0
        self._queue_entries = []
        self._queue_deltas = {}
        self._last_stats = None
        self._pending_complete = None
        self._stopping = False
        self._finished = False
        self._deferred = {}
        self._child_counts = {}
        self._open_paths = {()}
        self._node_override = {}
        self._open_on_insert = set()
        self._placeholder_of = {}
        self._scanning = True
        self._pre_search_open = None
        self._after_queue_label = None

        root_node = FileNode(Path(path).name or str(path), 0, True)
        root_id = self.tree.insert('', 'end', text=root_node.name,
            values=self._node_values(root_node, 0), tags=('dir',), open=True)
        self.item_to_node[root_id] = root_node
        self.path_to_item[()] = root_id
        self.item_to_path[root_id] = ()

        self.total_label.config(text="0 B")
        self.count_label.config(text="1")
        self.depth_label.config(text="0")
        self.scan_label.config(text="正在扫描...")
        self.path_label.config(text=path)
        self._clear_stats()
        self._clear_uncounted()
        self.browse_btn.config(state='disabled')
        self.pause_btn.config(state='normal', text="暂停")
        self.stop_btn.config(state='normal')
        if not self.progress.winfo_ismapped():
            self.progress.pack(side='right')
        self.progress.start(12)

        self.scan_generation = self.analysis.start(Path(path))
        self._schedule_event_poll()

    def _schedule_event_poll(self, delay: int = 60, idle: bool = False):
        if self._poll_job is not None:
            return
        # idle=True：一有空就接着干，不走定时器。
        # Windows 定时器精度只有 15 毫秒左右，用 after(1) 等于每次都白等 30 毫秒。
        if idle:
            self._poll_job = self.root.after_idle(self._poll_analysis_events)
        else:
            self._poll_job = self.root.after(delay, self._poll_analysis_events)

    def _poll_analysis_events(self):
        """一次轮询 = 一小片活。干完就交还控制权，窗口才能拖动不卡。"""
        self._poll_job = None
        started = time.monotonic()
        deadline = started + self.SLICE_SECONDS

        events = self.analysis.drain_events()
        for event in events:
            if event.get('generation') != self.scan_generation:
                continue
            if event['type'] == 'batch':
                self._queue_entries.extend(event['entries'])
                deltas = self._queue_deltas
                for parent_path, delta in event['deltas'].items():
                    deltas[parent_path] = deltas.get(parent_path, 0) + delta
                counts = self._child_counts
                for parent_path, number in (event.get('counts') or {}).items():
                    counts[parent_path] = counts.get(parent_path, 0) + number
                self._last_stats = event
            elif event['type'] == 'complete':
                self._pending_complete = event
        if self._last_stats is not None:
            self.total_bytes = self._last_stats['bytes']

        self._insert_slice(deadline)
        self._apply_queued_deltas()

        if self._pending_complete is not None and not self._queue_entries:
            event = self._pending_complete
            self._pending_complete = None
            self.on_analysis_complete(event)

        if self._scanning:
            self._refresh_header()
        if not self._queue_entries:
            self._open_on_insert.clear()

        if self._queue_entries or self.analysis.running or events:
            self._schedule_event_poll(idle=True)
        elif self._scanning and not self.analysis.events.empty():
            # 扫描线程说自己跑完了，但"完成"事件刚好在这几毫秒里才塞进队列 —— 下一轮再收尾。
            # 不然状态栏会一直挂着"正在扫描"，数字和类型统计也永远不落地。
            self._schedule_event_poll(idle=True)
        elif self._fill_open_folders():
            # 列表上还留着"开着却空着"的文件夹，接着摆
            self._schedule_event_poll(idle=True)
        else:
            self._finished = True
            if self._after_queue_label:
                self.scan_label.config(text=self._after_queue_label)
                self._after_queue_label = None

    def _node_values(self, node: FileNode, total: int):
        total = total or self.total_bytes or node.size
        pct = (node.size / total * 100.0) if total else 0.0
        return (format_size(node.size), f"{pct:.1f}%")

    def _node_tags(self, node: FileNode):
        return ('dir' if node.is_dir else 'file',)

    def _placeholder_label(self, path, exact: Optional[int] = None) -> str:
        """占位行上写什么字。

        这条行不是给你看的进度条，是 Tk 8.6 的老规矩：一个文件夹"底下有没有东西"，
        只看它下面挂没挂子行 —— 不垫一条，展开箭头就画不出来。
        平时它是藏着的（父文件夹收着就看不见），点开的那一下真内容立刻摆上来，它就被撤了。
        所以真被看到的时候，写的必须是真话：这个文件夹里有多少项 ——
        扫描已经数出来的就写数出来的，实在还没数到才写"正在扫描"。
        """
        if exact is not None:
            return f"{exact:,} 项"
        number = self._child_counts.get(path)
        if number:
            return f"{number:,} 项"
        return '正在扫描…' if self._scanning else '空'

    def _insert_slice(self, deadline):
        """把排队的条目往列表里摆，摆到这一片的时间用完为止。

        只摆"父文件夹已经展开"的条目 —— 折叠着看不见的行，摆了也是白摆。
        这是提速的关键：扫描时真正要摆的只有顶层那几十行，剩下的等点开再摆。
        """
        entries = self._queue_entries
        path_to_item = self.path_to_item
        open_paths = self._open_paths
        deferred = self._deferred
        overrides = self._node_override
        open_on_insert = self._open_on_insert
        placeholders = self._placeholder_of
        total = len(entries)
        index = 0
        while index < total and time.monotonic() < deadline:
            parent_path, node_path, name, size, is_dir = entries[index]
            index += 1
            if node_path in path_to_item:
                overrides.pop(node_path, None)
                continue
            if parent_path not in open_paths:
                deferred.setdefault(parent_path, []).append((name, size, is_dir))
                continue
            parent_id = path_to_item.get(parent_path)
            if parent_id is None:
                # 父行还没摆上，先存着，别丢了
                deferred.setdefault(parent_path, []).append((name, size, is_dir))
                continue
            node = overrides.pop(node_path, None)
            # 拿到真节点 = 它里面有几项是准数（搜索/展开全部重摆时走这条）
            exact = len(node.children) if node is not None else None
            if node is None:
                node = FileNode(name, size, is_dir)
            item_id = self.tree.insert(parent_id, 'end', text=name,
                values=self._node_values(node, self.total_bytes),
                tags=self._node_tags(node), open=node_path in open_on_insert)
            path_to_item[node_path] = item_id
            self.item_to_path[item_id] = node_path
            self.item_to_node[item_id] = node
            # 真内容进来了，父文件夹底下那条占位行可以撤了
            ph = placeholders.pop(parent_path, None)
            if ph is not None:
                self.tree.delete(ph)
            # 底下垫一条占位行，展开箭头才画得出来；里面确实是空的就不用垫了
            if is_dir and exact != 0 and node_path not in placeholders:
                placeholders[node_path] = self.tree.insert(item_id, 'end',
                    text=self._placeholder_label(node_path, exact),
                    values=('', ''), tags=('placeholder',))
            depth = len(node_path)
            if depth > self.live_max_depth:
                self.live_max_depth = depth
        if index:
            del entries[:index]

    def _load_children(self, path):
        """把一个文件夹的下一层排进队，等主线程分片摆上去。返回排了几条。"""
        parent_id = self.path_to_item.get(path)
        if parent_id is None:
            return 0
        queued = self._queue_entries
        added = 0
        pending = self._deferred.pop(path, None)
        if pending:
            # 扫描还没走完，先用手头暂存的
            for name, size, is_dir in pending:
                child_path = path + (name,)
                if child_path in self.path_to_item:
                    continue
                queued.append((path, child_path, name, size, is_dir))
                added += 1
            return added
        # 扫描已经完事，直接用最终结果里的
        node = self.item_to_node.get(parent_id)
        if node is not None:
            for child in node.children:
                child_path = path + (child.name,)
                if child_path in self.path_to_item:
                    continue
                queued.append((path, child_path, child.name, child.size, child.is_dir))
                self._node_override[child_path] = child
                added += 1
        return added

    def on_tree_open(self, event):
        path = self._just_opened_path()
        if path is not None:
            self._open_paths.add(path)   # 后头新摆上来的行才知道该往它里面放
            if self._load_children(path):
                self._finished = False
                self._schedule_event_poll(idle=True)
            else:
                self._drop_placeholder_if_empty(path)
        self._fill_open_folders()

    def _fill_open_folders(self) -> bool:
        """自查一遍：列表上凡是"开着、里面却只挂着占位行"的文件夹，都把它内容摆出来。

        正常点开那一下就已经摆好了，这一步纯粹是防漏 ——
        认错行、事件没发出来、账记错了，这些情况全兜得住，反正"开着却空着"就是不对。
        摆了新东西就返回 True（调用方据此接着排轮询）。
        """
        queued = False
        for path, placeholder in list(self._placeholder_of.items()):
            item_id = self.path_to_item.get(path)
            if item_id is None or not self._is_open_row(item_id):
                continue
            if tuple(self.tree.get_children(item_id)) != (placeholder,):
                continue
            self._open_paths.add(path)
            if self._load_children(path):
                self._finished = False
                queued = True
            else:
                self._drop_placeholder_if_empty(path)
        return queued

    def _just_opened_path(self):
        """刚被展开的是哪个文件夹。

        别拿"程序账上记着谁开着"（_open_paths）当准 —— 收起来的时候认错行，账就会记错，
        照账办事就会出现"点开了却一直只显示占位行"的怪事。
        列表上的真实样子才作数：一个行开着、里面却只剩那条垫底占位行 ——
        它就是刚被点开、真内容还没摆上来的那个。
        """
        for item_id in (self._last_click_item, self.tree.focus()):
            if self._is_open_row(item_id) and self._is_content_pending(item_id):
                return self.item_to_path[item_id]
        for path, placeholder in self._placeholder_of.items():
            item_id = self.path_to_item.get(path)
            if item_id is not None and self._is_open_row(item_id) and (
                    tuple(self.tree.get_children(item_id)) == (placeholder,)):
                return path
        # 都认不出来就凑合一下：开着的那行本来就没什么要加载的，点一下也不会出错
        for item_id in (self._last_click_item, self.tree.focus()):
            if self._is_open_row(item_id):
                return self.item_to_path[item_id]
        return None

    def _is_open_row(self, item_id) -> bool:
        if not item_id or item_id not in self.item_to_path:
            return False
        return bool(self.tree.item(item_id, 'open'))

    def _is_content_pending(self, item_id) -> bool:
        """这个行开着，但里面只有那条垫底占位行 = 真内容还没摆上来。"""
        path = self.item_to_path.get(item_id)
        placeholder = self._placeholder_of.get(path)
        return placeholder is not None and tuple(self.tree.get_children(item_id)) == (placeholder,)

    def _drop_placeholder_if_empty(self, path):
        """里面确实没东西，才把占位行撤掉（箭头跟着消失）。

        扫描还没走到它头上时先别撤 —— 撤早了箭头也没了，用户就再也点不开这个文件夹。
        """
        if self._scanning:
            return
        node = self.item_to_node.get(self.path_to_item.get(path))
        if not node or not node.children:
            self._drop_placeholder(path)

    def _drop_placeholder(self, path):
        """撤掉某个文件夹底下垫着的那条占位行。"""
        ph = self._placeholder_of.pop(path, None)
        if ph is not None:
            self.tree.delete(ph)

    def on_tree_close(self, event):
        """收起来了：账上抹一笔，后头新扫到的东西就别再往它里面摆了。

        认错行不要紧（多留一笔只是白干点活），所以只在"确实收起来了"的行上抹账，
        绝不把还开着的文件夹从账上抹掉 —— 那才是"点开不加载"的病根。
        """
        for item_id in (self._last_click_item, self.tree.focus()):
            path = self.item_to_path.get(item_id) if item_id else None
            if path is None or self._is_open_row(item_id):
                continue
            self._open_paths.discard(path)
            return

    def on_tree_click(self, event):
        self._last_click_item = self.tree.identify_row(event.y)
        self.tip.cancel()

    def after_tree_click(self, event):
        """手点完之后兜个底。

        万一点开的事件压根没发出来，或者认错了行，这里也能凭"列表上开着却空着"把它补上。
        """
        if self.current_root is None:
            return
        if self._fill_open_folders():
            self._schedule_event_poll(idle=True)

    def _collect_visible(self, open_paths):
        """按"哪些文件夹是展开的"，算出该显示哪些行，从浅到深排好。"""
        rows = []
        stack = [(self.current_root, ())]
        while stack:
            node, rel_path = stack.pop()
            for child in node.children:
                child_path = rel_path + (child.name,)
                rows.append((len(child_path), child_path, rel_path, child))
                if child.is_dir and child_path in open_paths:
                    stack.append((child, child_path))
        rows.sort(key=lambda row: row[0])
        return rows

    def _rebuild_lazy(self, open_paths):
        """把列表清空重摆：只摆 open_paths 里那些文件夹的下一层。"""
        self._rebuild_lazy_from(self._collect_visible(open_paths), open_paths, open_paths)

    def _rebuild_lazy_from(self, rows, open_paths, open_on_insert=None):
        self._reset_tree()
        self._open_paths = set(open_paths) | {()}
        self._open_on_insert = set() if open_on_insert is None else set(open_on_insert)
        queued = self._queue_entries
        for _depth, child_path, rel_path, child in rows:
            queued.append((rel_path, child_path, child.name, child.size, child.is_dir))
            self._node_override[child_path] = child
        self._finished = False
        self._schedule_event_poll(idle=True)

    def _reset_tree(self):
        self.tree.delete(*self.tree.get_children())
        self.item_to_node.clear()
        self.path_to_item.clear()
        self.item_to_path.clear()
        self._queue_entries = []
        self._node_override.clear()
        self._deferred = {}
        self._placeholder_of = {}
        self._open_on_insert = set()
        self.live_max_depth = 0
        root = self.current_root
        name = root.name if root else ''
        root_node = FileNode(name, root.size if root else 0, True)
        root_id = self.tree.insert('', 'end', text=name,
            values=self._node_values(root_node, root.size if root else 1),
            tags=('dir',), open=True)
        self.item_to_node[root_id] = root if root else root_node
        self.path_to_item[()] = root_id
        self.item_to_path[root_id] = ()
        self.show_full_path(None)       # 摆新列表了，状态栏先退回"整个文件夹"
        # 根下面也先垫一条（展开箭头），等真行摆上来就撤掉
        if root is not None and root.children:
            self._placeholder_of[()] = self.tree.insert(root_id, 'end',
                text=self._placeholder_label((), len(root.children)),
                values=('', ''), tags=('placeholder',))

    def _apply_queued_deltas(self):
        """把攒下来的大小加到各级父节点上。数字全加，但标签每轮最多刷 200 个。"""
        deltas = self._queue_deltas
        if not deltas:
            return
        self._queue_deltas = {}
        path_to_item = self.path_to_item
        item_to_node = self.item_to_node
        touched = set()
        for parent_path, delta in deltas.items():
            current = parent_path
            while True:
                item_id = path_to_item.get(current)
                if item_id is not None:
                    node = item_to_node.get(item_id)
                    if node is not None:
                        node.size += delta
                        touched.add(current)
                if not current:
                    break
                current = current[:-1]
        if not touched:
            return
        for path in list(touched)[:self.MAX_LABEL_REFRESH]:
            item_id = path_to_item[path]
            node = item_to_node[item_id]
            self.tree.item(item_id, text=node.name,
                values=self._node_values(node, self.total_bytes),
                tags=self._node_tags(node))

    def _refresh_header(self):
        stats = self._last_stats
        if stats is None:
            return
        if self._stopping:
            text = "正在停止..."
        elif self.analysis.paused:
            text = f"已暂停  ·  {stats['files']:,} 个文件"
        else:
            error_text = f"  ·  读取失败 {stats['errors']} 项" if stats['errors'] else ""
            text = f"正在扫描 {stats['files']:,} 个文件{error_text}"
        self.total_label.config(text=format_size(stats['bytes']))
        self.count_label.config(text=f"{stats['files'] + stats['directories']:,}")
        self.depth_label.config(text=str(self.live_max_depth))
        self.scan_label.config(text=text)

    def toggle_pause(self):
        if not self.analysis.running:
            return
        if self.analysis.paused:
            self.analysis.resume()
            self.pause_btn.config(text="暂停")
        else:
            self.analysis.pause()
            self.pause_btn.config(text="继续")
        self._refresh_header()

    def stop_analysis(self):
        if not self.analysis.running:
            return
        self._stopping = True
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
        self.total_bytes = root.size
        self._adopt_final_tree(root)
        self.total_label.config(text=format_size(root.size))
        self.count_label.config(text=f"{event['files'] + event['directories']:,}")
        self.depth_label.config(text=str(event.get('depth', 0)))
        self._render_stats(event.get('stats') or {})
        status = "已停止" if event['cancelled'] else "扫描完成"
        bits = [status, f"{event['files']:,} 个文件"]
        if event['errors']:
            kinds = event.get('error_kinds') or {}
            damaged = sum(n for code, n in kinds.items() if code in DAMAGE_CODES)
            bits.append(f"{event['errors']} 项读不到")
            if damaged:
                bits.append(f"其中 {damaged} 项是文件系统损坏")
        self.scan_label.config(text="  ·  ".join(bits))
        self._fill_uncounted(event.get('symlinks', 0), event['errors'], event.get('error_kinds'))
        self._stopping = False
        self._scanning = False

    def _render_stats(self, ext_stats):
        """把扫描线程顺手攒好的"文件类型统计"画出来。

        一行三样，每样都带单位，不许让人猜：
          · 占全部 —— 这类文件的大小占总共的百分之几（真占比，不是跟最大那类比）
          · 文件数 —— 这类文件有多少个（后面带个"个"字）
          · 大小   —— 这类文件加起来多大（后面带 B / KB / MB / GB）
        以前是"一根柱子 + 两个光秃秃的数字"，柱长还是拿最大那类当满格比的，
        标题却写"占比 / 数量 / 大小" —— 新用户看了一准懵。
        """
        total = self.total_bytes or sum(size for _count, size in ext_stats.values()) or 1
        rows = sorted(ext_stats.items(), key=lambda kv: kv[1][1], reverse=True)[:self.STATS_TOP]
        kinds = len(ext_stats)
        if kinds > len(rows):
            self.stats_hint.config(text=f"只列最大的 {len(rows)} 类，共 {kinds} 类")
        else:
            self.stats_hint.config(text=f"共 {kinds} 类，全在这儿了")
        self.stats_text.config(state='normal')
        self.stats_text.delete(1.0, 'end')
        if not rows:
            self.stats_text.insert('end', "里面一个文件都没有\n", 'dim')
        else:
            self.stats_text.insert('end', self._stats_header(), 'head')
            for ext, (count, size) in rows:
                label = '.' + ext
                if display_width(label) > self.STATS_NAME_WIDTH:
                    label = label[:self.STATS_NAME_WIDTH - 1] + '…'
                self.stats_text.insert('end', pad_to(label, self.STATS_NAME_WIDTH), 'name')
                self.stats_text.insert('end', pad_left(self._percent_text(size, total), 7), 'accent')
                self.stats_text.insert('end', f"{count:>8,} 个", 'num')
                self.stats_text.insert('end', f"{format_size(size):>11}\n", 'num')
        self.stats_text.config(state='disabled')

    @staticmethod
    def _percent_text(size: int, total: int) -> str:
        """占比怎么显示。小到不足 0.1% 的就写"<0.1%"，别写成"0.0%"让人以为算错了。"""
        percent = size / total * 100.0
        if percent <= 0:
            return "0%"
        if percent < 0.1:
            return "<0.1%"
        return f"{percent:.1f}%"

    def _stats_header(self) -> str:
        """表头。凑巧的是：不写单位反而最清楚 —— 数字自己都带着单位呢。"""
        return (pad_to('类型', self.STATS_NAME_WIDTH) + pad_left('占全部', 7)
                + pad_left('文件数', 10) + pad_left('大小', 11) + '\n')

    def _fill_uncounted(self, symlinks, errors, error_kinds=None):
        """只列真正没算进总数里的东西，并说清楚是"没权限"还是"盘坏了"。"""
        self.uncounted_text.config(state='normal')
        self.uncounted_text.delete(1.0, 'end')
        kinds = error_kinds or {}
        top = sorted(kinds.items(), key=lambda kv: -kv[1])[:3]
        if not symlinks and not errors:
            self.uncounted_text.insert('end', "无 —— 所有文件都算进去了\n", 'dim')
        else:
            if symlinks:
                self.uncounted_text.insert('end', pad_to('符号链接', 15), 'name')
                self.uncounted_text.insert('end', f"{symlinks:>6,} 个", 'num')
                self.uncounted_text.insert('end', "   不跟进去，防止绕圈\n", 'dim')
            for code, count in top:
                name = ERROR_NAMES.get(code, f'其他错误 {code}')
                why = ERROR_LABELS.get(code, '')
                self.uncounted_text.insert('end', pad_to(name, 15), 'name')
                self.uncounted_text.insert('end', f"{count:>6,} 项", 'num')
                tag = 'warn' if code in DAMAGE_CODES else 'dim'
                self.uncounted_text.insert('end', f"   {why}\n", tag)
            if len(kinds) > len(top):
                self.uncounted_text.insert('end', f"还有 {len(kinds) - len(top)} 类其他错误\n", 'dim')
        self.uncounted_text.config(state='disabled')

    def _adopt_final_tree(self, root):
        """把最终结果接到列表上：只走"列表上真的有那一行"的支路。

        折叠着的支路在列表上根本没有行，整支跳过 —— 所以收尾不会卡。
        """
        total = root.size or 1
        path_to_item = self.path_to_item
        item_to_node = self.item_to_node
        tree = self.tree

        def adopt(node, rel_path):
            item_id = path_to_item.get(rel_path)
            if not item_id:
                return
            item_to_node[item_id] = node
            tree.item(item_id, text=node.name,
                values=self._node_values(node, total), tags=self._node_tags(node))
            ph = self._placeholder_of.get(rel_path)
            if ph is not None:
                if node.children:
                    # 扫完了就有准数：占位行改写成"里面有多少项"
                    tree.item(ph, text=self._placeholder_label(rel_path, len(node.children)))
                else:
                    # 空文件夹：把占位行撤了，箭头也就跟着消失
                    del self._placeholder_of[rel_path]
                    tree.delete(ph)
            desired = []
            for child in node.children:
                child_path = rel_path + (child.name,)
                child_id = path_to_item.get(child_path)
                if child_id is None:
                    continue      # 这一行没摆在列表上，它下面更不可能有，整支跳过
                desired.append(child_id)
                adopt(child, child_path)
            # 顺序本来就对就不动 —— 否则要为每个节点白调一次 Tcl
            # （比的时候把垫底那条占位行排除掉，它本来就不该算在"内容"里）
            if desired and tuple(desired) != tuple(
                    row for row in tree.get_children(item_id) if row != ph):
                for index, child_id in enumerate(desired):
                    tree.move(child_id, item_id, index)

        adopt(root, ())
        # 最终结果已经在内存里了，暂存的那份可以扔了，省内存
        self._deferred = {}
        self._node_override = {}

    def _clear_stats(self):
        self.stats_text.config(state='normal')
        self.stats_text.delete(1.0, 'end')
        self.stats_text.insert('end', "扫描完成后在这里显示\n", 'dim')
        self.stats_text.config(state='disabled')
        self.stats_hint.config(text="扫描完成后显示")

    def _clear_uncounted(self):
        self.uncounted_text.config(state='normal')
        self.uncounted_text.delete(1.0, 'end')
        self.uncounted_text.insert('end', "扫描完成后在这里显示\n", 'dim')
        self.uncounted_text.config(state='disabled')

    # ---------- 交互 ----------

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
        if not self.current_root:
            return
        # 把所有文件夹都标记成"展开"，交给懒加载去摆行 —— 分片摆，不会一下卡住
        all_dirs = set()
        stack = [((), self.current_root)]
        while stack:
            path, node = stack.pop()
            for child in node.children:
                child_path = path + (child.name,)
                if child.is_dir:
                    all_dirs.add(child_path)
                    stack.append((child_path, child))
        self.scan_label.config(text="正在展开全部...")
        self._after_queue_label = "已展开全部"
        self._rebuild_lazy(all_dirs)

    def collapse_all(self):
        self._open_paths = {()}
        def close(item):
            self.tree.item(item, open=False)
            for child in self.tree.get_children(item):
                close(child)
        for item in self.tree.get_children(''):
            close(item)

    def open_structure(self):
        """开结构图窗口：一张图看全局 —— 谁占地方（方块图）/ 啥结构（思维导图）。"""
        if self.current_root is None:
            self.scan_label.config(text="先挑一个文件夹扫完，再看结构图")
            return
        if self.structure_window is not None:
            self.structure_window.win.deiconify()
            self.structure_window.win.lift()
            return
        self.structure_window = StructureWindow(self)

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
        """搜索 = 只把命中的行（加上它们的上级文件夹）摆出来，不再把整棵树推倒重来。"""
        if not self.current_root:
            return
        query = self.search_entry.get().strip().lower()
        if not query:
            self.clear_search()
            return

        # 一次走完内存里的结果，收集"该显示的行"，顺带把命中的上级也标上
        rows = []
        keep = set()
        stack = [(self.current_root, ())]
        while stack:
            node, rel_path = stack.pop()
            if query in node.name.lower():
                current = rel_path
                while True:
                    if current in keep:
                        break
                    keep.add(current)
                    if not current:
                        break
                    current = current[:-1]
            if node.is_dir:
                for child in node.children:
                    stack.append((child, rel_path + (child.name,)))

        # 命中的上级 + 命中本身，从浅到深；懒加载只摆这些
        stack = [(self.current_root, ())]
        while stack:
            node, rel_path = stack.pop()
            if rel_path and rel_path in keep:
                rows.append((len(rel_path), rel_path, rel_path[:-1], node))
            if node.is_dir and rel_path in keep:
                for child in node.children:
                    stack.append((child, rel_path + (child.name,)))
        rows.sort(key=lambda row: row[0])

        # 哪些文件夹需要展开：只要它的下一层还有命中/上级行要显示，就得展开
        need_open = set()
        for path in keep:
            if path:
                need_open.add(path[:-1])

        if self._pre_search_open is None:
            self._pre_search_open = set(self._open_paths)
        self.scan_label.config(text="正在显示搜索结果...")
        self._after_queue_label = f"搜索 “{query}” · 找到 {len(keep) - 1:,} 项"
        self._rebuild_lazy_from(rows, need_open, need_open)

    def clear_search(self):
        if not self.current_root:
            return
        self.search_entry.delete(0, 'end')
        restore = self._pre_search_open if self._pre_search_open is not None else {()}
        self._pre_search_open = None
        self.scan_label.config(text="正在恢复列表...")
        self._after_queue_label = "已清除搜索"
        self._rebuild_lazy(restore)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    app = FolderSizeGUI()
    app.run()
