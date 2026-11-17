cmake_minimum_required(VERSION 3.24)
project(automa_native_sim LANGUAGES C CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
include(FetchContent)
set(USE_TLS OFF CACHE BOOL "Loopback prototype" FORCE)
set(USE_ZLIB OFF CACHE BOOL "No WebSocket compression" FORCE)
set(JSON_BuildTests OFF CACHE BOOL "" FORCE)
FetchContent_Declare(ixwebsocket
  URL https://codeload.github.com/machinezone/IXWebSocket/tar.gz/refs/tags/v11.4.6
  URL_HASH SHA256=c024334f8e45980836c67008979a884d6dcc5ef067dd2eb1fa7241f4c17ddc32)
FetchContent_Declare(json
  URL https://codeload.github.com/nlohmann/json/tar.gz/refs/tags/v3.11.3
  URL_HASH SHA256=0d8ef5af7f9794e3263480193c491549b2ba6cc74bb018906202ada498a79406)
FetchContent_MakeAvailable(ixwebsocket json)
find_package(JPEG REQUIRED)
find_package(Threads REQUIRED)
add_library(sim_core src/world.cpp src/render.cpp src/protocol.cpp)
target_include_directories(sim_core PUBLIC src)
target_link_libraries(sim_core PUBLIC nlohmann_json::nlohmann_json JPEG::JPEG Threads::Threads)
target_compile_options(sim_core PRIVATE -Wall -Wextra -Wpedantic)
add_executable(automa-native-sim src/main.cpp src/server.cpp)
target_link_libraries(automa-native-sim PRIVATE sim_core ixwebsocket)
target_compile_definitions(automa-native-sim PRIVATE DEFAULT_CONFIG="${CMAKE_CURRENT_SOURCE_DIR}/scenarios/chaser-depth-obstacles.json")
enable_testing()
add_executable(native_sim_tests tests/core.cpp)
target_link_libraries(native_sim_tests PRIVATE sim_core)
add_test(NAME native_sim_core COMMAND native_sim_tests ${CMAKE_CURRENT_SOURCE_DIR}/tests/reference.json ${CMAKE_CURRENT_SOURCE_DIR}/scenarios/chaser-depth-obstacles.json)
add_executable(native_sim_regression tests/regression.cpp)
target_link_libraries(native_sim_regression PRIVATE sim_core)
add_test(NAME native_sim_regression COMMAND native_sim_regression
  ${CMAKE_CURRENT_SOURCE_DIR}/scenarios/chaser-depth-obstacles.json
  ${CMAKE_CURRENT_SOURCE_DIR}/tests/native-baseline.json)
