#!/usr/bin/env python3
"""
文件夹大小分析器 - 冰柱图可视化 v2
Cyberpunk 霓虹风格
"""

import os
import sys
from pathlib import Path
from dataclasses import dataclass
from typing import List
import json
import html as html_lib

IGNORE_DEFAULT = {"node_modules", ".git", "__pycache__", ".vscode", "bin", "obj", ".idea", "dist", "build"}


@dataclass
class FileNode:
    name: str
    size: int
    is_dir: bool
    children: List['FileNode']

    def total_size(self) -> int:
        return self.size


def format_size(bytes_size: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    unit_idx = 0
    size = float(bytes_size)
    while size >= 1024.0 and unit_idx < len(units) - 1:
        size /= 1024.0
        unit_idx += 1
    if unit_idx == 0:
        return f"{int(size)} B"
    return f"{size:.2f} {units[unit_idx]}"


def analyze_directory(path: Path, ignore_patterns: set, max_depth: int = -1, current_depth: int = 0) -> FileNode:
    node = FileNode(name=path.name or str(path), size=0, is_dir=True, children=[])
    if max_depth >= 0 and current_depth >= max_depth:
        return node
    try:
        # Iterate directly: the final size sort makes an up-front name sort redundant.
        with os.scandir(path) as entries:
            for entry in entries:
                name = entry.name
                if name in ignore_patterns:
                    continue
                if '.' in ignore_patterns and name.startswith('.'):
                    continue
                try:
                    if entry.is_symlink():
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        child = analyze_directory(Path(entry.path), ignore_patterns, max_depth, current_depth + 1)
                        node.children.append(child)
                        node.size += child.size
                    elif entry.is_file(follow_symlinks=False):
                        size = entry.stat(follow_symlinks=False).st_size
                        node.children.append(FileNode(name=name, size=size, is_dir=False, children=[]))
                        node.size += size
                except (PermissionError, FileNotFoundError, OSError):
                    continue
    except (PermissionError, FileNotFoundError, NotADirectoryError, OSError):
        pass
    node.children.sort(key=lambda child: child.size, reverse=True)
    return node


def build_hierarchy(node: FileNode) -> dict:
    return {
        "name": node.name,
        "size": 0 if node.is_dir else node.size,
        "children": [build_hierarchy(c) for c in node.children]
    }


HTML_TEMPLATE = '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title} - 冰柱图</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700;900&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
    <script src="https://d3js.org/d3.v7.min.js"></script>
    <style>
        :root {{
            --bg-deep: #0a0a0f;
            --bg-surface: #12121a;
            --bg-elevated: #1a1a24;
            --neon-cyan: #00f5ff;
            --neon-magenta: #ff00aa;
            --neon-lime: #b8ff00;
            --neon-orange: #ff6b35;
            --text-primary: #ffffff;
            --text-secondary: #8a8a9a;
            --glow-cyan: 0 0 20px rgba(0, 245, 255, 0.5);
            --glow-magenta: 0 0 20px rgba(255, 0, 170, 0.5);
        }}
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: 'JetBrains Mono', monospace;
            background: var(--bg-deep);
            color: var(--text-primary);
            min-height: 100vh;
            overflow-x: hidden;
        }}
        body::before {{
            content: '';
            position: fixed;
            top: 0; left: 0;
            width: 100%; height: 100%;
            background-image:
                linear-gradient(rgba(0, 245, 255, 0.03) 1px, transparent 1px),
                linear-gradient(90deg, rgba(0, 245, 255, 0.03) 1px, transparent 1px);
            background-size: 50px 50px;
            pointer-events: none;
            z-index: 0;
        }}
        body::after {{
            content: '';
            position: fixed;
            top: 0; left: 0;
            width: 100%; height: 100%;
            background: url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noise'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23noise)'/%3E%3C/svg%3E");
            opacity: 0.03;
            pointer-events: none;
            z-index: 1;
        }}
        .header {{
            position: relative;
            z-index: 10;
            padding: 40px 60px;
            background: linear-gradient(180deg, var(--bg-surface) 0%, transparent 100%);
            border-bottom: 1px solid rgba(0, 245, 255, 0.1);
        }}
        .header-content {{
            max-width: 1400px;
            margin: 0 auto;
        }}
        .title-wrapper {{
            display: flex;
            align-items: baseline;
            gap: 20px;
            margin-bottom: 16px;
        }}
        h1 {{
            font-family: 'Orbitron', sans-serif;
            font-size: 2.5rem;
            font-weight: 900;
            letter-spacing: 0.1em;
            background: linear-gradient(135deg, var(--neon-cyan), var(--neon-magenta));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
            filter: drop-shadow(0 0 30px rgba(0, 245, 255, 0.3));
        }}
        .subtitle {{
            font-size: 0.85rem;
            color: var(--text-secondary);
            letter-spacing: 0.3em;
            text-transform: uppercase;
        }}
        .stats-bar {{
            display: flex;
            gap: 40px;
            padding: 20px 0;
        }}
        .stat {{
            display: flex;
            flex-direction: column;
            gap: 4px;
        }}
        .stat-label {{
            font-size: 0.7rem;
            color: var(--text-secondary);
            letter-spacing: 0.2em;
            text-transform: uppercase;
        }}
        .stat-value {{
            font-family: 'Orbitron', sans-serif;
            font-size: 1.4rem;
            font-weight: 700;
            color: var(--neon-cyan);
            text-shadow: var(--glow-cyan);
        }}
        .stat-value.accent {{
            color: var(--neon-magenta);
            text-shadow: var(--glow-magenta);
        }}
        .chart-container {{
            position: relative;
            z-index: 5;
            padding: 20px;
            min-height: calc(100vh - 200px);
            overflow: auto;
        }}
        #chart {{
            width: 100%;
            height: calc(100vh - 200px);
        }}
        #chart svg {{
            display: block;
        }}
        .node rect {{
            cursor: pointer;
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        }}
        .node rect:hover {{
            filter: brightness(1.3) drop-shadow(0 0 15px currentColor);
            z-index: 100;
        }}
        .node {{
            cursor: pointer;
        }}
        .node text {{
            font-family: 'JetBrains Mono', monospace;
            font-size: 11px;
            font-weight: 500;
            fill: var(--text-primary);
            pointer-events: none;
            text-shadow: 0 2px 8px rgba(0, 0, 0, 0.8);
            opacity: 0;
            transition: opacity 0.3s ease;
        }}
        .node:hover text {{ opacity: 1; }}
        .tooltip {{
            position: fixed;
            background: var(--bg-elevated);
            border: 1px solid var(--neon-cyan);
            padding: 16px 20px;
            border-radius: 4px;
            font-size: 13px;
            pointer-events: none;
            opacity: 0;
            transition: opacity 0.15s ease;
            z-index: 1000;
            box-shadow: 0 0 30px rgba(0, 245, 255, 0.3), inset 0 0 20px rgba(0, 245, 255, 0.05);
            backdrop-filter: blur(10px);
            min-width: 220px;
            max-width: 300px;
        }}
        .tooltip.visible {{ opacity: 1; transform: scale(1.02); }}
        .tooltip-name {{
            font-family: 'Orbitron', sans-serif;
            font-size: 14px;
            font-weight: 700;
            color: var(--neon-cyan);
            margin-bottom: 10px;
            padding-bottom: 8px;
            border-bottom: 1px solid rgba(0, 245, 255, 0.2);
        }}
        .tooltip-row {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin: 6px 0;
        }}
        .tooltip-label {{
            color: var(--text-secondary);
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 0.1em;
        }}
        .tooltip-value {{ font-weight: 500; color: var(--text-primary); }}
        .tooltip-value.size {{ color: var(--neon-lime); }}
        .tooltip-value.percent {{ color: var(--neon-orange); }}
        .legend {{
            position: fixed;
            bottom: 30px;
            right: 30px;
            background: var(--bg-surface);
            border: 1px solid rgba(0, 245, 255, 0.2);
            padding: 16px 20px;
            border-radius: 4px;
            z-index: 100;
            opacity: 0.8;
            transition: opacity 0.3s ease;
        }}
        .legend:hover {{ opacity: 1; }}
        .legend-title {{
            font-size: 10px;
            color: var(--text-secondary);
            letter-spacing: 0.2em;
            text-transform: uppercase;
            margin-bottom: 10px;
        }}
        .legend-item {{
            display: flex;
            align-items: center;
            gap: 8px;
            margin: 6px 0;
            font-size: 11px;
        }}
        .legend-color {{ width: 14px; height: 14px; border-radius: 2px; }}

        /* File Type Stats Panel */
        .stats-panel {{
            position: fixed;
            top: 30px;
            right: 30px;
            background: var(--bg-surface);
            border: 1px solid rgba(0, 245, 255, 0.2);
            padding: 0;
            border-radius: 4px;
            z-index: 100;
            width: 280px;
            max-height: calc(100vh - 200px);
            overflow: hidden;
            display: flex;
            flex-direction: column;
        }}
        .stats-panel-header {{
            padding: 14px 16px;
            border-bottom: 1px solid rgba(0, 245, 255, 0.1);
            cursor: pointer;
            display: flex;
            justify-content: space-between;
            align-items: center;
            transition: background 0.2s;
        }}
        .stats-panel-header:hover {{
            background: rgba(0, 245, 255, 0.05);
        }}
        .stats-panel-title {{
            font-size: 10px;
            color: var(--text-secondary);
            letter-spacing: 0.2em;
            text-transform: uppercase;
        }}
        .stats-panel-toggle {{
            color: var(--neon-cyan);
            font-size: 12px;
            transition: transform 0.3s;
        }}
        .stats-panel.collapsed .stats-panel-toggle {{
            transform: rotate(180deg);
        }}
        .stats-panel-content {{
            padding: 12px 16px;
            overflow-y: auto;
            flex: 1;
        }}
        .stats-panel.collapsed .stats-panel-content {{
            display: none;
        }}
        .file-type-row {{
            display: flex;
            align-items: center;
            padding: 8px 0;
            border-bottom: 1px solid rgba(255,255,255,0.05);
        }}
        .file-type-row:last-child {{
            border-bottom: none;
        }}
        .file-type-ext {{
            font-weight: 500;
            color: var(--neon-cyan);
            min-width: 50px;
        }}
        .file-type-bar {{
            flex: 1;
            height: 6px;
            background: rgba(255,255,255,0.1);
            border-radius: 3px;
            margin: 0 10px;
            overflow: hidden;
        }}
        .file-type-bar-fill {{
            height: 100%;
            background: linear-gradient(90deg, var(--neon-cyan), var(--neon-magenta));
            border-radius: 3px;
            transition: width 0.5s ease;
        }}
        .file-type-count {{
            font-size: 11px;
            color: var(--text-secondary);
            min-width: 45px;
            text-align: right;
        }}
        .file-type-size {{
            font-size: 11px;
            color: var(--neon-lime);
            min-width: 70px;
            text-align: right;
        }}

        /* Filter controls */
        .filter-section {{
            padding: 12px 16px;
            border-top: 1px solid rgba(0, 245, 255, 0.1);
        }}
        .filter-label {{
            font-size: 10px;
            color: var(--text-secondary);
            letter-spacing: 0.1em;
            text-transform: uppercase;
            margin-bottom: 8px;
            display: block;
        }}
        .filter-input {{
            width: 100%;
            background: var(--bg-deep);
            border: 1px solid rgba(0, 245, 255, 0.2);
            border-radius: 4px;
            padding: 8px 12px;
            color: var(--text-primary);
            font-family: inherit;
            font-size: 12px;
            outline: none;
            transition: border-color 0.2s;
        }}
        .filter-input:focus {{
            border-color: var(--neon-cyan);
        }}
        .filter-hint {{
            font-size: 10px;
            color: var(--text-secondary);
            margin-top: 6px;
        }}

        .scanline {{
            position: fixed;
            top: 0; left: 0;
            width: 100%; height: 100%;
            background: linear-gradient(transparent 50%, rgba(0, 245, 255, 0.02) 50%);
            background-size: 100% 4px;
            pointer-events: none;
            z-index: 2;
            opacity: 0.3;
        }}
        .node rect {{
            opacity: 0;
            animation: fadeIn 0.6s ease forwards;
        }}
        @keyframes fadeIn {{
            from {{ opacity: 0; transform: translateY(10px); }}
            to {{ opacity: 1; transform: translateY(0); }}
        }}
        @media (max-width: 768px) {{
            .header {{ padding: 20px 30px; }}
            h1 {{ font-size: 1.5rem; }}
            .stats-bar {{ gap: 20px; }}
            .stat-value {{ font-size: 1.1rem; }}
            .chart-container {{ padding: 20px; }}
        }}
    </style>
</head>
<body>
    <div class="scanline"></div>
    <header class="header">
        <div class="header-content">
            <div class="title-wrapper">
                <h1>冰柱图</h1>
                <span class="subtitle">文件夹大小分析</span>
            </div>
            <div class="stats-bar">
                <div class="stat">
                    <span class="stat-label">总大小</span>
                    <span class="stat-value" id="total-size">{total_size_display}</span>
                </div>
                <div class="stat">
                    <span class="stat-label">文件 / 文件夹</span>
                    <span class="stat-value accent" id="total-count">{node_count}</span>
                </div>
                <div class="stat">
                    <span class="stat-label">最大深度</span>
                    <span class="stat-value" id="max-depth">{max_depth}</span>
                </div>
            </div>
        </div>
    </header>
    <div class="chart-container">
        <div id="chart"></div>
    </div>
    <div class="legend">
        <div class="legend-title">层级</div>
        <div class="legend-item"><div class="legend-color" style="background: #00f5ff;"></div><span>第 1 层</span></div>
        <div class="legend-item"><div class="legend-color" style="background: #ff00aa;"></div><span>第 2 层</span></div>
        <div class="legend-item"><div class="legend-color" style="background: #b8ff00;"></div><span>第 3 层</span></div>
        <div class="legend-item"><div class="legend-color" style="background: #ff6b35;"></div><span>第 4 层+</span></div>
    </div>

    <div class="stats-panel" id="stats-panel">
        <div class="stats-panel-header" onclick="toggleStatsPanel()">
            <span class="stats-panel-title">文件类型统计</span>
            <span class="stats-panel-toggle">▼</span>
        </div>
        <div class="stats-panel-content" id="stats-panel-content">
            <!-- Populated by JS -->
        </div>
        <div class="filter-section">
            <label class="filter-label">最小尺寸 (KB)</label>
            <input type="number" class="filter-input" id="min-size-filter" value="0" min="0">
            <div class="filter-hint">隐藏小于此尺寸的文件</div>
        </div>
    </div>

    <div class="tooltip" id="tooltip">
        <div class="tooltip-name" id="tooltip-name">--</div>
        <div class="tooltip-row">
            <span class="tooltip-label">大小</span>
            <span class="tooltip-value size" id="tooltip-size">--</span>
        </div>
        <div class="tooltip-row">
            <span class="tooltip-label">占比</span>
            <span class="tooltip-value percent" id="tooltip-percent">--</span>
        </div>
        <div class="tooltip-row">
            <span class="tooltip-label">类型</span>
            <span class="tooltip-value" id="tooltip-type">--</span>
        </div>
    </div>
    <script>
        const jsonData = {json_data};
        const totalSize = {total_size};

        const colors = ['#00f5ff', '#ff00aa', '#b8ff00', '#ff6b35', '#a855f7', '#22d3ee'];

        function formatBytes(bytes) {{
            const units = ['B', 'KB', 'MB', 'GB', 'TB'];
            let unitIdx = 0;
            let size = bytes;
            while (size >= 1024 && unitIdx < units.length - 1) {{
                size /= 1024;
                unitIdx++;
            }}
            return size.toFixed(unitIdx === 0 ? 0 : 2) + ' ' + units[unitIdx];
        }}

        // File type statistics
        function getFileTypes(node, stats) {{
            if (!node.children || node.children.length === 0) {{
                // It's a file
                const name = node.name;
                const ext = name.includes('.') ? name.split('.').pop().toLowerCase() : 'no_ext';
                if (!stats[ext]) stats[ext] = {{ count: 0, size: 0 }};
                stats[ext].count++;
                stats[ext].size += node.size;
            }} else {{
                node.children.forEach(child => getFileTypes(child, stats));
            }}
        }}

        const fileStats = {{}};
        getFileTypes(jsonData, fileStats);

        // Sort by size and take top 10
        const sortedTypes = Object.entries(fileStats)
            .sort((a, b) => b[1].size - a[1].size)
            .slice(0, 12);

        const maxTypeSize = sortedTypes.length > 0 ? sortedTypes[0][1].size : 1;

        // Render stats panel
        const statsContent = document.getElementById('stats-panel-content');
        sortedTypes.forEach(([ext, data]) => {{
            const percent = (data.size / maxTypeSize * 100).toFixed(1);
            const row = document.createElement('div');
            row.className = 'file-type-row';
            row.innerHTML = `
                <span class="file-type-ext">.${{ext}}</span>
                <div class="file-type-bar"><div class="file-type-bar-fill" style="width:${{percent}}%"></div></div>
                <span class="file-type-count">${{data.count}}</span>
                <span class="file-type-size">${{formatBytes(data.size)}}</span>
            `;
            statsContent.appendChild(row);
        }});

        function toggleStatsPanel() {{
            document.getElementById('stats-panel').classList.toggle('collapsed');
        }}

        // Filtering
        let minSizeKB = 0;
        let filteredData = null;

        function filterBySize(node, minBytes) {{
            if (!node.children || node.children.length === 0) {{
                return node.size >= minBytes ? node : null;
            }}
            const filteredChildren = node.children
                .map(child => filterBySize(child, minBytes))
                .filter(child => child !== null);
            if (filteredChildren.length === 0 && node.size < minBytes) {{
                return null;
            }}
            return {{
                ...node,
                children: filteredChildren,
                size: filteredChildren.reduce((sum, c) => sum + c.size, 0)
            }};
        }}

        // Tree layout settings - horizontal layout
        const nodeWidth = 180;
        const nodeHeight = 60;
        const horizontalSpacing = 40;
        const verticalSpacing = 25;

        function rebuildChart(data) {{
            g.selectAll('*').remove();

            // Create hierarchy
            const root = d3.hierarchy(data)
                .sum(d => d.size)
                .sort((a, b) => b.value - a.value);

            // Create tree layout - horizontal (swap x and y)
            const treeLayout = d3.tree()
                .nodeSize([verticalSpacing + nodeHeight, horizontalSpacing + nodeWidth])
                .separation((a, b) => a.parent === b.parent ? 1 : 1.3);

            treeLayout(root);

            // Calculate bounds
            let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
            root.each(d => {{
                if (d.x < x0) x0 = d.x;
                if (d.x > x1) x1 = d.x;
                if (d.y < y0) y0 = d.y;
                if (d.y > y1) y1 = d.y;
            }});

            const treeWidth = x1 - x0 + nodeWidth;
            const treeHeight = y1 - y0 + nodeHeight;

            // Update SVG size to fit tree
            const chartWidth = Math.max(width - margin.left - margin.right, treeWidth + 100);
            const chartHeight = Math.max(height - margin.top - margin.bottom, treeHeight + 100);
            svg.attr('width', chartWidth).attr('height', chartHeight);

            // Center the tree - offset for left-to-right layout
            const offsetX = -x0 + margin.left + 30;
            const offsetY = -y0 + margin.top + 50;

            // Draw connecting lines - horizontal layout
            g.selectAll('.link')
                .data(root.links())
                .join('path')
                .attr('class', 'link')
                .attr('d', d => {{
                    // Swap x and y for horizontal layout
                    const sourceX = d.source.x + nodeHeight + offsetX;
                    const sourceY = d.source.y + nodeWidth / 2 + offsetY;
                    const targetX = d.target.x + offsetX;
                    const targetY = d.target.y + nodeWidth / 2 + offsetY;
                    const midX = (sourceX + targetX) / 2;
                    return `M${{sourceX}},${{sourceY}} C${{midX}},${{sourceY}} ${{midX}},${{targetY}} ${{targetX}},${{targetY}}`;
                }})
                .attr('fill', 'none')
                .attr('stroke', 'rgba(0, 245, 255, 0.3)')
                .attr('stroke-width', 2);

            // Create node groups - horizontal layout
            const nodes = g.selectAll('.node')
                .data(root.descendants())
                .join('g')
                .attr('class', 'node')
                .attr('transform', d => `translate(${{d.x + offsetX}},${{d.y + offsetY}})`);

            // Node background rect - vertical rectangle for horizontal layout
            nodes.append('rect')
                .attr('x', 0)
                .attr('y', 0)
                .attr('width', nodeHeight)
                .attr('height', nodeWidth)
                .attr('fill', d => colorScale(d.depth))
                .attr('rx', 6)
                .attr('ry', 6)
                .style('cursor', 'pointer')
                .style('filter', 'drop-shadow(0 2px 8px rgba(0,0,0,0.3))')
                .on('mouseenter', (event, d) => {{
                    tooltip.classList.add('visible');
                    tooltip.querySelector('#tooltip-name').textContent = d.data.name;
                    tooltip.querySelector('#tooltip-size').textContent = formatBytes(d.value);
                    tooltip.querySelector('#tooltip-percent').textContent = ((d.value / totalSize) * 100).toFixed(2) + '%';
                    tooltip.querySelector('#tooltip-type').textContent = d.children ? '文件夹' : '文件';

                    // Highlight path
                    g.selectAll('.link').attr('stroke', l =>
                        dAncestors(d).includes(l.target) ? 'rgba(0, 245, 255, 0.8)' : 'rgba(0, 245, 255, 0.15)'
                    );
                    nodes.select('rect').attr('opacity', n =>
                        dAncestors(d).includes(n) ? 1 : 0.4
                    );
                }})
                .on('mousemove', (event) => {{
                    let x = event.clientX + 25;
                    let y = event.clientY - 10;
                    const rect = tooltip.getBoundingClientRect();
                    if (x + rect.width > window.innerWidth - 20) x = event.clientX - rect.width - 25;
                    if (y + rect.height > window.innerHeight - 20) y = event.clientY - rect.height - 10;
                    tooltip.style.left = x + 'px';
                    tooltip.style.top = y + 'px';
                }})
                .on('mouseleave', () => {{
                    tooltip.classList.remove('visible');
                    g.selectAll('.link').attr('stroke', 'rgba(0, 245, 255, 0.3)');
                    nodes.select('rect').attr('opacity', 1);
                }});

            // Node name label - vertical text for horizontal layout
            nodes.append('text')
                .attr('x', nodeHeight / 2)
                .attr('y', nodeWidth / 2 - 6)
                .attr('text-anchor', 'middle')
                .attr('fill', '#fff')
                .attr('font-size', '12px')
                .attr('font-weight', '500')
                .attr('writing-mode', 'vertical-rl')
                .attr('text-orientation', 'mixed')
                .style('pointer-events', 'none')
                .text(d => {{
                    const maxChars = 6;
                    return d.data.name.length > maxChars
                        ? d.data.name.substring(0, maxChars - 2) + '..'
                        : d.data.name;
                }});

            // Node size label
            nodes.append('text')
                .attr('x', nodeHeight / 2)
                .attr('y', nodeWidth / 2 + 12)
                .attr('text-anchor', 'middle')
                .attr('fill', 'rgba(255,255,255,0.7)')
                .attr('font-size', '10px')
                .style('pointer-events', 'none')
                .text(d => formatBytes(d.value));

            // Update max depth
            let newMaxDepth = 0;
            root.each(d => {{ if (d.depth > newMaxDepth) newMaxDepth = d.depth; }});
            document.getElementById('max-depth').textContent = newMaxDepth;

            // Store offset for resize
            window.treeOffset = {{ x: offsetX, y: offsetY }};
        }}

        function dAncestors(node) {{
            const ancestors = [];
            let current = node;
            while (current) {{ ancestors.push(current); current = current.parent; }}
            return ancestors;
        }}

        document.getElementById('min-size-filter').addEventListener('input', (e) => {{
            minSizeKB = parseInt(e.target.value) || 0;
            const minBytes = minSizeKB * 1024;
            filteredData = filterBySize(JSON.parse(JSON.stringify(jsonData)), minBytes);
            rebuildChart(filteredData);
        }});

        // Initial setup
        document.getElementById('total-size').textContent = formatBytes(totalSize);

        let nodeCount = 0;
        let maxDepth = 0;
        function countNodes(node, depth) {{
            nodeCount++;
            maxDepth = Math.max(maxDepth, depth);
            if (node.children) node.children.forEach(child => countNodes(child, depth + 1));
        }}
        countNodes(jsonData, 0);
        document.getElementById('total-count').textContent = nodeCount;
        document.getElementById('max-depth').textContent = maxDepth;

        const width = document.getElementById('chart').clientWidth;
        const height = document.getElementById('chart').clientHeight;
        const margin = {{ top: 40, right: 40, bottom: 40, left: 40 }};

        const svg = d3.select('#chart').append('svg');
        const g = svg.append('g');

        const colorScale = d3.scaleOrdinal().domain([0,1,2,3,4,5]).range(colors);

        const tooltip = document.getElementById('tooltip');

        rebuildChart(jsonData);

        window.addEventListener('resize', () => {{
            const newWidth = document.getElementById('chart').clientWidth;
            const newHeight = document.getElementById('chart').clientHeight;
            rebuildChart(filteredData || jsonData);
        }});
    </script>
</body>
</html>'''


def render_html(node: FileNode, output_path: str, max_depth: int = 0) -> bool:
    hierarchy = build_hierarchy(node)
    total_size = node.total_size()

    # 统计节点数
    def count_nodes(n, depth):
        cnt = 1
        for c in n.children:
            cnt += count_nodes(c, depth + 1)
        return cnt
    node_count = count_nodes(node, 0)

    safe_json = (json.dumps(hierarchy)
                 .replace('<', r'\u003c')
                 .replace('>', r'\u003e')
                 .replace('&', r'\u0026'))
    html_content = HTML_TEMPLATE.format(
        title=html_lib.escape(node.name),
        json_data=safe_json,
        total_size=total_size,
        total_size_display=format_size(total_size),
        node_count=node_count,
        max_depth=max_depth
    )

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html_content)
    return True


def main():
    import argparse
    parser = argparse.ArgumentParser(description="文件夹大小分析器 - 冰柱图可视化")
    parser.add_argument('path', nargs='?', help='要分析的目录路径')
    parser.add_argument('-o', '--output', default='icicle.html', help='HTML 输出文件')
    parser.add_argument('-d', '--depth', type=int, default=-1, help='最大递归深度')
    parser.add_argument('-i', '--ignore', action='append', default=[], help='忽略的目录')
    parser.add_argument('--no-ignore-hidden', action='store_true', help='不忽略隐藏文件/目录')
    parser.add_argument('--ignore-default', action='store_true', help='使用默认忽略列表')

    args = parser.parse_args()
    if not args.path:
        parser.print_help()
        return 1

    target_path = Path(args.path)
    if not target_path.exists() or not target_path.is_dir():
        print(f"错误: '{args.path}' 不是有效的目录", file=sys.stderr)
        return 1

    ignore_set = set(args.ignore) if args.ignore else set()
    if args.ignore_default:
        ignore_set.update(IGNORE_DEFAULT)
    if not args.no_ignore_hidden:
        ignore_set.add('.')

    print(f"正在分析: {target_path}", file=sys.stderr)
    root = analyze_directory(target_path, ignore_set, args.depth)

    html_path = args.output
    if render_html(root, html_path):
        print(f"HTML 冰柱图已生成: {html_path}")
        print("请在浏览器中打开查看")

    return 0


if __name__ == "__main__":
    sys.exit(main())
