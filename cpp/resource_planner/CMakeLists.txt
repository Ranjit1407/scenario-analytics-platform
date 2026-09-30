cmake_minimum_required(VERSION 3.16)
project(resource_planner LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
  set(CMAKE_BUILD_TYPE Release)
endif()

if(MSVC)
  add_compile_options(/W4 /permissive-)
else()
  add_compile_options(-Wall -Wextra -Wpedantic -Wconversion)
endif()

find_package(Threads REQUIRED)

# Planning logic as a library so the console app and the tests share it.
add_library(planner_core src/planner.cpp)
target_include_directories(planner_core PUBLIC include)

add_executable(resource_planner src/main.cpp)
target_link_libraries(resource_planner PRIVATE planner_core Threads::Threads)

enable_testing()
add_executable(planner_tests tests/planner_tests.cpp)
target_link_libraries(planner_tests PRIVATE planner_core)
add_test(NAME planner_tests COMMAND planner_tests)
