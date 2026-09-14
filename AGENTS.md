# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Project Overview

文件夹大小分析器 (Folder Size Analyzer) - A tool for analyzing and visualizing directory/file sizes with icicle chart visualizations. Implemented in both C++ and Python.

## Build Commands

### C++ Version
```bash
# Build with CMake
mkdir build && cd build
cmake ..
cmake --build .

# Run
./file_size_analyzer.exe <path> [options]
# Example: ./file_size_analyzer.exe C:\Projects -o result.html -d 3
```

### Python Version
```bash
# CLI with HTML output
python file_size_analyzer.py <path> -o output.html

# GUI (Tkinter)
python file_size_gui.py
```

## Architecture

### C++ Core (`file_size_analyzer.cpp/hpp`)
- `FileNode`: Tree node structure (name, size, isDir, children, parent)
- `FileSizeAnalyzer`: Recursive directory analyzer with ignore patterns and max depth

### C++ Visualization (`icicle_plot.cpp/hpp`)
- `IciclePlot::renderASCII()`: ASCII art waterfall chart
- `IciclePlot::renderHTML()`: D3.js-based interactive icicle chart (冰柱图)

### Python GUI (`file_size_gui.py`)
- Tkinter-based neon/cyberpunk styled interface
- Treeview with virtual scrolling for large directories
- Real-time progress, pause/stop controls, search filtering
- File type statistics panel

## Key Design Decisions

- Uses `std::filesystem::directory_iterator` for cross-platform directory traversal
- Default ignore patterns: `node_modules`, `.git`, `__pycache__`, `.vscode`, `bin`, `obj`
- Results sorted by size (descending) at every level
- Python GUI uses `os.scandir()` for performance (caches stat results)
