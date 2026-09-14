cmake_minimum_required(VERSION 3.10)
project(FileSizeAnalyzer)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_executable(file_size_analyzer
    main.cpp
    file_size_analyzer.cpp
    icicle_plot.cpp
)

if(MINGW)
    target_link_options(file_size_analyzer PRIVATE -static -static-libgcc -static-libstdc++)
endif()
