#pragma once
#include <cstdint>
#include <filesystem>
#include <string>
#include <vector>

namespace fs = std::filesystem;

struct FileNode {
    std::string name;
    uint64_t size = 0; // Cached aggregate size for directories.
    bool isDir = false;
    std::vector<FileNode> children;

    uint64_t totalSize() const;
};

class FileSizeAnalyzer {
public:
    FileSizeAnalyzer();
    FileNode analyze(const std::string& path, bool ignoreHidden = true);
    void addIgnorePattern(const std::string& pattern);
    void setMaxDepth(int depth);

private:
    std::vector<std::string> ignorePatterns_;
    int maxDepth_ = -1;
    bool ignoreHidden_ = true;

    void analyzeRecursive(const fs::path& path, FileNode& node, int currentDepth);
    bool shouldIgnore(const std::string& name) const;
};
