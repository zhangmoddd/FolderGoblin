#pragma once
#include "file_size_analyzer.hpp"
#include <string>
#include <vector>

class IciclePlot {
public:
    static std::string renderASCII(const FileNode& root, int maxWidth = 80);
    static std::string renderHTML(const FileNode& root, const std::string& outputPath);

private:
    struct PlotRect {
        std::string name;
        uint64_t size;
        int depth;
        int x, width;
    };

    static void collectRects(const FileNode& node, std::vector<PlotRect>& rects, int depth);
    static std::string formatSize(uint64_t bytes);
    static std::string generateHTML(const FileNode& root, uint64_t totalSize, const std::string& title);
};
