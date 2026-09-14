#include <iostream>
#include <string>
#include <cstring>
#include <filesystem>
#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#endif
#include "file_size_analyzer.hpp"
#include "icicle_plot.hpp"

namespace fs = std::filesystem;

#ifdef _WIN32
bool launchGui() {
    wchar_t executablePath[32768] = {};
    const DWORD executableLength = GetModuleFileNameW(nullptr, executablePath, 32768);
    if (executableLength == 0 || executableLength >= 32768) {
        MessageBoxW(nullptr, L"无法获取程序路径。", L"文件夹大小分析器", MB_OK | MB_ICONERROR);
        return false;
    }

    const fs::path workingDirectory = fs::path(executablePath).parent_path();
    const fs::path scriptPath = workingDirectory / L"file_size_gui.py";
    if (!fs::exists(scriptPath)) {
        const std::wstring message = L"未找到 GUI 脚本：\n" + scriptPath.wstring();
        MessageBoxW(nullptr, message.c_str(), L"文件夹大小分析器", MB_OK | MB_ICONERROR);
        return false;
    }

    wchar_t pythonPath[32768] = {};
    const DWORD pythonLength = SearchPathW(nullptr, L"pythonw.exe", nullptr, 32768, pythonPath, nullptr);
    if (pythonLength == 0 || pythonLength >= 32768) {
        MessageBoxW(nullptr, L"未找到 pythonw.exe，请确认 Python 已加入 PATH。", L"文件夹大小分析器", MB_OK | MB_ICONERROR);
        return false;
    }

    std::wstring commandLine = L"\"" + std::wstring(pythonPath) + L"\" \"" + scriptPath.wstring() + L"\"";
    STARTUPINFOW startupInfo{};
    startupInfo.cb = sizeof(startupInfo);
    PROCESS_INFORMATION processInfo{};
    const std::wstring workingDirectoryText = workingDirectory.wstring();
    const BOOL created = CreateProcessW(
        nullptr,
        commandLine.data(),
        nullptr,
        nullptr,
        FALSE,
        CREATE_UNICODE_ENVIRONMENT,
        nullptr,
        workingDirectoryText.c_str(),
        &startupInfo,
        &processInfo
    );
    if (!created) {
        const std::wstring message = L"GUI 启动失败，错误代码：" + std::to_wstring(GetLastError());
        MessageBoxW(nullptr, message.c_str(), L"文件夹大小分析器", MB_OK | MB_ICONERROR);
        return false;
    }

    CloseHandle(processInfo.hThread);
    CloseHandle(processInfo.hProcess);
    return true;
}
#endif

void printUsage(const char* programName) {
    std::cout << R"(
用法: )" << programName << R"( [选项] <目录路径>

选项:
  -o, --output <文件>     输出 HTML 文件 (默认: icicle.html)
  -d, --depth <N>         最大递归深度 (默认: 无限制)
  -i, --ignore <目录>     忽略指定目录 (可多次使用)
  --no-ignore-hidden      不忽略隐藏文件/目录
  -a, --ascii             仅输出 ASCII 冰柱图
      --html              输出 HTML 冰柱图 (默认)
  --help                  显示此帮助信息

示例:
  )" << programName << R"( /path/to/folder
  )" << programName << R"( -o result.html -d 3 /path/to/folder
  )" << programName << R"( --ignore node_modules --ignore .git /path/to/folder
)";
}

int main(int argc, char* argv[]) {
    if (argc < 2) {
#ifdef _WIN32
        return launchGui() ? 0 : 1;
#else
        printUsage(argv[0]);
        return 1;
#endif
    }

    std::string targetPath;
    std::string outputPath = "icicle.html";
    int maxDepth = -1;
    bool outputASCII = false;
    bool outputHTML = true;
    bool ignoreHidden = true;
    std::vector<std::string> ignoreDirs = {"node_modules", ".git", "__pycache__", ".vscode", "bin", "obj"};

    // 解析参数
    for (int i = 1; i < argc; i++) {
        std::string arg = argv[i];

        if (arg == "--help" || arg == "-h") {
            printUsage(argv[0]);
            return 0;
        } else if (arg == "-o" || arg == "--output") {
            if (i + 1 < argc) outputPath = argv[++i];
        } else if (arg == "-d" || arg == "--depth") {
            if (i + 1 < argc) maxDepth = std::stoi(argv[++i]);
        } else if (arg == "-i" || arg == "--ignore") {
            if (i + 1 < argc) ignoreDirs.push_back(argv[++i]);
        } else if (arg == "--no-ignore-hidden") {
            ignoreHidden = false;
        } else if (arg == "-a" || arg == "--ascii") {
            outputASCII = true;
            outputHTML = false;
        } else if (arg == "--html") {
            outputHTML = true;
            outputASCII = false;
        } else if (arg[0] != '-') {
            targetPath = arg;
        }
    }

    if (targetPath.empty()) {
        std::cerr << "错误: 请指定要分析的目录路径\n";
        printUsage(argv[0]);
        return 1;
    }

    if (!fs::exists(targetPath) || !fs::is_directory(targetPath)) {
        std::cerr << "错误: '" << targetPath << "' 不是有效的目录\n";
        return 1;
    }

    // 分析目录
    FileSizeAnalyzer analyzer;
    for (const auto& dir : ignoreDirs) {
        analyzer.addIgnorePattern(dir);
    }
    if (maxDepth >= 0) {
        analyzer.setMaxDepth(maxDepth);
    }

    FileNode root = analyzer.analyze(targetPath, ignoreHidden);

    // 输出结果
    if (outputASCII) {
        std::cout << IciclePlot::renderASCII(root) << "\n";
    }

    if (outputHTML) {
        std::string htmlPath = IciclePlot::renderHTML(root, outputPath);
        if (!htmlPath.empty()) {
            std::cout << "HTML 冰柱图已生成: " << htmlPath << "\n";
            std::cout << "请在浏览器中打开查看\n";
        } else {
            std::cerr << "错误: 无法生成 HTML 文件\n";
            return 1;
        }
    }

    return 0;
}
