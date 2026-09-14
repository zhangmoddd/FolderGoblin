# 文件夹大小分析器

看某个文件夹到底被什么占满了。选一个目录，它把里面每个子文件夹、每个文件占多大，从大到小排给你看。

## 怎么用

1. 打开 **`FolderSizeAnalyzer` 文件夹**，双击里面的 **`FolderSizeAnalyzer.exe`**
2. 点"选择文件夹"，挑你要看的目录
3. 等它扫完。列表里大的排前面，一眼看出谁最占地方

不需要装 Python，也不需要装别的，双击就能跑。

> ⚠️ `FolderSizeAnalyzer.exe` 和它旁边的 `_internal` 文件夹是**一整套**。
> 要拷给别人，就把整个 `FolderSizeAnalyzer` 文件夹一起拷，只拷 exe 会打不开。

## 有什么功能

- 边扫边显示，扫大目录不会卡死
- 可以暂停 / 继续 / 停止
- 按名字搜索过滤
- 右侧显示文件类型统计（哪类文件最占地方）
- 默认跳过 `node_modules`、`.git`、`build`、`__pycache__`、`.idea`、`dist` 这些目录——它们是缓存和依赖，数量多、体积虚高，不算你的真实数据

## 文件说明

| 文件 | 是什么 |
|---|---|
| `FolderSizeAnalyzer\` | 程序本体。双击里面的 `FolderSizeAnalyzer.exe` 就能用（已打包，不需要 Python） |
| `file_size_gui.py` | 程序源码，全部逻辑就这一个文件 |
| `start.bat` | 用源码启动（电脑装了 Python 时可用，改完代码想马上看效果就用它） |
| `test_file_size_analyzer.py` | 自动测试，改完代码跑一下确认没改坏 |

## 改代码之后

```bash
# 1. 跑测试，确认没扫坏
python -m unittest test_file_size_analyzer

# 2. 直接看效果（不打包）
python file_size_gui.py

# 3. 重新打包（输出到项目根目录的 FolderSizeAnalyzer 文件夹）
python -m PyInstaller --noconsole --onedir --name FolderSizeAnalyzer --distpath . --workpath _build_tmp --specpath _build_tmp --noconfirm file_size_gui.py
# 打完把里面那个旧文件夹整个替换掉

> ⚠️ 必须用 `--onedir`（文件夹版）。不要用 `--onefile`（单文件版）——单文件每次双击都要
> 先把 11MB 解开摊到临时目录，启动要多等好几秒。
```

## 技术说明（不了解也别硬看）

- Python 3，**只用标准库**（tkinter 画界面 + os.scandir 扫盘），没有任何第三方依赖
- 扫描在后台线程做（`AnalysisThread`），用 `os.scandir()` 流式扫描，分批把结果推给界面，所以界面不卡
- 界面是霓虹 / 赛博朋克风格，深色底
- 大小是"累积值"：文件夹自身的大小 = 里面所有东西加起来（目录节点直接缓存，不用每次重算）

> 历史版本：这个项目原先同时写了 C++ 命令行版和 Python 命令行版，三套代码功能重复。
> 2026-09-15 精简为只保留 Python 窗口版，旧代码在 Git 标签 `v0.1` / `pre-refactor` 里随时能翻出来。
