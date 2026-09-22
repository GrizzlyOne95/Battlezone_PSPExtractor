cmake_minimum_required(VERSION 3.14)
project(bzpsp_reference CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_library(bzpsp_reference STATIC bzpsp_tank.cpp bzpsp_combat.cpp bzpsp_match.cpp)
target_include_directories(bzpsp_reference PUBLIC ${CMAKE_CURRENT_SOURCE_DIR})

add_executable(test_reference test_reference.cpp)
target_link_libraries(test_reference PRIVATE bzpsp_reference)
