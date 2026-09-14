#include "icicle_plot.hpp"
#include <algorithm>
#include <sstream>
#include <iomanip>
#include <fstream>
#include <cmath>
#include <functional>


namespace {
std::string escapeJson(const std::string& value) {
    std::ostringstream escaped;
    for (const unsigned char ch : value) {
        switch (ch) {
            case '\\': escaped << "\\\\"; break;
            case '"': escaped << "\\\""; break;
            case '\b': escaped << "\\b"; break;
            case '\f': escaped << "\\f"; break;
            case '\n': escaped << "\\n"; break;
            case '\r': escaped << "\\r"; break;
            case '\t': escaped << "\\t"; break;
            case '<': escaped << "\\u003c"; break;
            case '>': escaped << "\\u003e"; break;
            case '&': escaped << "\\u0026"; break;
            default:
                if (ch < 0x20) {
                    escaped << "\\u" << std::hex << std::setw(4) << std::setfill('0')
                            << static_cast<int>(ch) << std::dec << std::setfill(' ');
                } else {
                    escaped << static_cast<char>(ch);
                }
        }
    }
    return escaped.str();
}

std::string escapeHtml(const std::string& value) {
    std::string escaped;
    escaped.reserve(value.size());
    for (const char ch : value) {
        switch (ch) {
            case '&': escaped += "&amp;"; break;
            case '<': escaped += "&lt;"; break;
            case '>': escaped += "&gt;"; break;
            case '"': escaped += "&quot;"; break;
            case '\'': escaped += "&#39;"; break;
            default: escaped += ch;
        }
    }
    return escaped;
}
}

std::string IciclePlot::formatSize(uint64_t bytes) {
    const char* units[] = {"B", "KB", "MB", "GB", "TB"};
    int unitIdx = 0;
    double size = static_cast<double>(bytes);

    while (size >= 1024.0 && unitIdx < 4) {
        size /= 1024.0;
        unitIdx++;
    }

    std::ostringstream oss;
    if (unitIdx == 0) {
        oss << size << " B";
    } else {
        oss << std::fixed << std::setprecision(2) << size << " " << units[unitIdx];
    }
    return oss.str();
}

void IciclePlot::collectRects(const FileNode& node, std::vector<PlotRect>& rects, int depth) {
    if (node.isDir && node.children.empty()) return;

    PlotRect rect;
    rect.name = node.name;
    rect.size = node.totalSize();
    rect.depth = depth;
    rects.push_back(rect);

    for (const auto& child : node.children) {
        collectRects(child, rects, depth + 1);
    }
}

std::string IciclePlot::renderASCII(const FileNode& root, int maxWidth) {
    std::vector<PlotRect> rects;
    collectRects(root, rects, 0);

    if (rects.empty()) return "";

    uint64_t totalSize = rects[0].size;
    if (totalSize == 0) return root.name + " (0 B)\n";
    int maxDepth = 0;
    for (const auto& r : rects) {
        maxDepth = std::max(maxDepth, r.depth);
    }

    // 计算每个深度的总宽度（基于最大深度分配）
    int rootWidth = maxWidth - 1;
    int depthWidth = rootWidth / (maxDepth + 1);

    std::ostringstream oss;

    // 标题行
    oss << "\n  " << root.name << " (" << formatSize(totalSize) << ")\n";
    oss << "  " << std::string(root.name.size() + formatSize(totalSize).size() + 3, '-') << "\n\n";

    // 收集每层的项目
    struct LayerItem {
        std::string name;
        uint64_t size;
        int width;
    };
    std::vector<std::vector<LayerItem>> layers(maxDepth + 1);

    for (const auto& r : rects) {
        if (r.depth == 0) continue;
        LayerItem item;
        item.name = r.name;
        item.size = r.size;
        item.width = std::max(1, static_cast<int>(std::round(
            static_cast<double>(r.size) / static_cast<double>(totalSize) * rootWidth)));
        layers[r.depth].push_back(item);
    }

    // 渲染每一层
    for (int d = 1; d <= maxDepth; d++) {
        // 深度指示
        oss << "  │";
        for (int i = 1; i < d; i++) oss << "  │   ";
        oss << "  ├─ ";

        // 绘制该层的条目
        for (const auto& item : layers[d]) {
            int barWidth = std::max(1, static_cast<int>(
                static_cast<double>(item.size) / static_cast<double>(totalSize) * rootWidth));

            oss << "█";
            for (int i = 1; i < barWidth; i++) oss << "█";
            oss << " ";
        }
        oss << "\n";

        // 名称行
        oss << "  │";
        for (int i = 1; i < d; i++) oss << "  │   ";
        oss << "  ├─ ";

        for (const auto& item : layers[d]) {
            int barWidth = std::max(1, static_cast<int>(
                static_cast<double>(item.size) / static_cast<double>(totalSize) * rootWidth));
            std::string label = item.name + " " + formatSize(item.size);
            int labelLen = std::min((int)label.size(), barWidth * 2);
            oss << label.substr(0, labelLen);
            if (labelLen < (int)label.size()) oss << "…";
            for (int i = labelLen; i < barWidth * 2 + 1; i++) oss << " ";
        }
        oss << "\n";
    }

    return oss.str();
}

std::string IciclePlot::generateHTML(const FileNode& root, uint64_t totalSize, const std::string& title) {
    std::ostringstream oss;
    oss << R"(<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>)" << escapeHtml(title) << R"( - 冰柱图</title>
    <script src="https://d3js.org/d3.v7.min.js"></script>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: #1a1a2e;
            color: #eee;
            min-height: 100vh;
        }
        .header {
            padding: 20px 40px;
            background: #16213e;
            border-bottom: 1px solid #0f3460;
        }
        .header h1 { font-size: 1.5rem; margin-bottom: 8px; }
        .header .total { color: #e94560; font-size: 1.1rem; }
        #chart { width: 100%; height: calc(100vh - 100px); }
        .node rect {
            cursor: pointer;
            stroke: #1a1a2e;
            stroke-width: 2px;
            transition: opacity 0.3s;
        }
        .node:hover rect { opacity: 0.8; }
        .node text {
            font-size: 12px;
            fill: #fff;
            pointer-events: none;
            text-shadow: 1px 1px 2px rgba(0,0,0,0.8);
        }
        .tooltip {
            position: absolute;
            background: #16213e;
            border: 1px solid #0f3460;
            padding: 10px 15px;
            border-radius: 8px;
            font-size: 14px;
            pointer-events: none;
            opacity: 0;
            transition: opacity 0.2s;
            z-index: 100;
        }
        .tooltip .name { font-weight: bold; margin-bottom: 4px; }
        .tooltip .size { color: #e94560; }
        .tooltip .percent { color: #888; font-size: 12px; }
    </style>
</head>
<body>
    <div class="header">
        <h1>)" << escapeHtml(title) << R"(</h1>
        <div class="total">总大小: )" << formatSize(totalSize) << R"(</div>
    </div>
    <div id="chart"></div>
    <div class="tooltip" id="tooltip"></div>
    <script>
)";

    // 将数据传递给 JavaScript
    oss << "        const data = ";

    // 构建 JSON 结构
    std::function<std::string(const FileNode&, int)> buildJSON = [&](const FileNode& node, int depth) -> std::string {
        std::ostringstream j;
        j << "{";
        j << "\"name\":\"" << escapeJson(node.name) << "\",";
        j << "\"size\":" << (node.isDir ? 0 : node.size) << ",";
        j << "\"depth\":" << depth << ",";
        j << "\"children\":[";
        for (size_t i = 0; i < node.children.size(); i++) {
            if (i > 0) j << ",";
            j << buildJSON(node.children[i], depth + 1);
        }
        j << "]";
        j << "}";
        return j.str();
    };

    oss << buildJSON(root, 0);
    oss << R"(;
        const totalSize = )" << totalSize << R"(;

        const escapeHtml = value => String(value).replace(/[&<>\"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[ch]));

        const width = document.getElementById('chart').clientWidth;
        const height = document.getElementById('chart').clientHeight;
        const margin = { top: 20, right: 20, bottom: 20, left: 20 };

        const color = d3.scaleOrdinal()
            .domain([0, 1, 2, 3, 4, 5])
            .range(["#e94560", "#0f3460", "#16213e", "#533483", "#2274a5", "#5fa777"]);

        const root = d3.hierarchy(data)
            .sum(d => d.size)
            .sort((a, b) => b.value - a.value);

        const partition = d3.partition()
            .size([width - margin.left - margin.right, height - margin.top - margin.bottom]);

        partition(root);

        const svg = d3.select("#chart")
            .append("svg")
            .attr("width", width)
            .attr("height", height)
            .append("g")
            .attr("transform", `translate(${margin.left},${margin.top})`);

        const tooltip = d3.select("#tooltip");

        const maxDepth = d3.max(root.descendants(), d => d.depth);

        svg.selectAll("rect")
            .data(root.descendants())
            .join("rect")
            .attr("class", "node")
            .attr("x", d => d.x0)
            .attr("y", d => (d.depth / (maxDepth + 1)) * (height - margin.top - margin.bottom))
            .attr("width", d => Math.max(0, d.x1 - d.x0 - 2))
            .attr("height", d => (height - margin.top - margin.bottom) / (maxDepth + 1) - 4)
            .attr("fill", d => color(d.depth))
            .on("mouseover", (event, d) => {
                tooltip.style("opacity", 1)
                    .html(`<div class="name">${escapeHtml(d.data.name)}</div>
                           <div class="size">${d3.format(",.0f")(d.value)} 字节</div>
                           <div class="percent">${((d.value / totalSize) * 100).toFixed(2)}%</div>`)
                    .style("left", (event.pageX + 15) + "px")
                    .style("top", (event.pageY - 10) + "px");
            })
            .on("mousemove", (event) => {
                tooltip.style("left", (event.pageX + 15) + "px")
                       .style("top", (event.pageY - 10) + "px");
            })
            .on("mouseout", () => {
                tooltip.style("opacity", 0);
            });

        svg.selectAll("text")
            .data(root.descendants())
            .join("text")
            .attr("x", d => d.x0 + 5)
            .attr("y", d => (d.depth / (maxDepth + 1)) * (height - margin.top - margin.bottom) + 20)
            .text(d => {
                const w = d.x1 - d.x0 - 4;
                const name = d.data.name;
                const maxChars = Math.floor(w / 8);
                return maxChars > 3 ? name.substring(0, maxChars - 2) + ".." : (maxChars > 0 ? name.substring(0, maxChars) : "");
            });

        window.addEventListener("resize", () => {
            const newWidth = document.getElementById('chart').clientWidth;
            svg.attr("width", newWidth);
            partition.size([newWidth - margin.left - margin.right, height - margin.top - margin.bottom]);
            partition(root);
        });
    </script>
</body>
</html>)";

    return oss.str();
}

std::string IciclePlot::renderHTML(const FileNode& root, const std::string& outputPath) {
    uint64_t totalSize = root.totalSize();
    if (totalSize == 0) return "";

    std::string html = generateHTML(root, totalSize, root.name);

    std::ofstream file(outputPath);
    if (!file) return "";

    file << html;
    file.close();

    return outputPath;
}
