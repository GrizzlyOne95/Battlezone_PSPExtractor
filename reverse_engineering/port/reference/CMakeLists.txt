cmake_minimum_required(VERSION 3.14)
project(bzpsp_reference CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_library(bzpsp_reference STATIC bzpsp_tank.cpp bzpsp_combat.cpp bzpsp_match.cpp bzpsp_collision.cpp)
target_include_directories(bzpsp_reference PUBLIC ${CMAKE_CURRENT_SOURCE_DIR})

add_executable(test_reference test_reference.cpp)
target_link_libraries(test_reference PRIVATE bzpsp_reference)

# Replays steps recorded from the game's own code (../validation/*_traces.py).
add_executable(test_golden test_golden.cpp)
target_link_libraries(test_golden PRIVATE bzpsp_reference)

enable_testing()
add_test(NAME behaviour COMMAND test_reference)
add_test(NAME golden_tank COMMAND test_golden tank ${CMAKE_CURRENT_SOURCE_DIR}/../golden/tank_traces.txt)
add_test(NAME golden_damage COMMAND test_golden damage ${CMAKE_CURRENT_SOURCE_DIR}/../golden/damage_traces.txt)
add_test(NAME golden_contact COMMAND test_golden contact ${CMAKE_CURRENT_SOURCE_DIR}/../golden/contact_traces.txt)
