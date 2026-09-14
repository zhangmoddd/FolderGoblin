#include "file_size_analyzer.hpp"
#include <algorithm>
#include <system_error>

FileSizeAnalyzer::FileSizeAnalyzer() = default;

uint64_t FileNode::totalSize() const {
    return size;
}

void FileSizeAnalyzer::addIgnorePattern(const std::string& pattern) {
    if (!pattern.empty()) {
        ignorePatterns_.push_back(pattern);
    }
}

void FileSizeAnalyzer::setMaxDepth(int depth) {
    maxDepth_ = depth;
}

bool FileSizeAnalyzer::shouldIgnore(const std::string& name) const {
    if (name.empty()) return false;
    if (ignoreHidden_ && name.front() == '.') return true;

    for (const auto& pattern : ignorePatterns_) {
        if (pattern.empty()) continue;
        if (name == pattern) return true;
        if (pattern.size() > 1 && pattern.front() == '*' && pattern.back() == '*') {
            const std::string substring = pattern.substr(1, pattern.size() - 2);
            if (name.find(substring) != std::string::npos) return true;
        } else if (pattern.front() == '*') {
            const std::string suffix = pattern.substr(1);
            if (name.size() >= suffix.size() &&
                name.compare(name.size() - suffix.size(), suffix.size(), suffix) == 0) {
                return true;
            }
        }
    }
    return false;
}

void FileSizeAnalyzer::analyzeRecursive(const fs::path& path, FileNode& node, int currentDepth) {
    if (maxDepth_ >= 0 && currentDepth >= maxDepth_) return;

    std::error_code ec;
    fs::directory_iterator iterator(path, fs::directory_options::skip_permission_denied, ec);
    const fs::directory_iterator end;
    while (!ec && iterator != end) {
        const fs::directory_entry entry = *iterator;
        iterator.increment(ec);

        const std::string filename = entry.path().filename().string();
        if (shouldIgnore(filename)) continue;

        std::error_code statusError;
        const fs::file_status status = entry.symlink_status(statusError);
        if (statusError || fs::is_symlink(status)) continue;

        if (fs::is_directory(status)) {
            FileNode child;
            child.name = filename;
            child.isDir = true;
            analyzeRecursive(entry.path(), child, currentDepth + 1);
            node.size += child.size;
            node.children.push_back(std::move(child));
        } else if (fs::is_regular_file(status)) {
            std::error_code sizeError;
            const auto fileSize = entry.file_size(sizeError);
            if (sizeError) continue;

            FileNode child;
            child.name = filename;
            child.isDir = false;
            child.size = fileSize;
            node.size += child.size;
            node.children.push_back(std::move(child));
        }
    }

    std::sort(node.children.begin(), node.children.end(),
              [](const FileNode& a, const FileNode& b) {
                  return a.size > b.size;
              });
}

FileNode FileSizeAnalyzer::analyze(const std::string& path, bool ignoreHidden) {
    ignoreHidden_ = ignoreHidden;
    FileNode root;
    root.name = fs::path(path).filename().string();
    if (root.name.empty()) root.name = path;
    root.isDir = true;
    analyzeRecursive(fs::path(path), root, 0);
    return root;
}
