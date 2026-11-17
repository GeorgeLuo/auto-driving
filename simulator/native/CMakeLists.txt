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
add_library(sim_core src/core/json.cpp)
target_include_directories(sim_core PUBLIC src)
target_link_libraries(sim_core PUBLIC nlohmann_json::nlohmann_json)

add_library(sim_scene src/scene/asset_loader.cpp src/scene/map_loader.cpp src/scene/material.cpp)
target_link_libraries(sim_scene PUBLIC sim_core)

add_library(sim_physics src/physics/vehicle.cpp)
target_link_libraries(sim_physics PUBLIC sim_scene sim_core)

# Each backend owns its implementation sources under src/render/<name>/.
file(GLOB renderer_sources CONFIGURE_DEPENDS "src/render/*/*.cpp")
add_library(sim_render ${renderer_sources})
target_link_libraries(sim_render PUBLIC sim_scene sim_core PRIVATE Threads::Threads)

add_library(sim_sensor src/sensor/camera.cpp src/sensor/jpeg.cpp)
target_link_libraries(sim_sensor PUBLIC sim_core PRIVATE JPEG::JPEG)

add_library(sim_protocol src/server/world.cpp src/server/protocol.cpp src/server/renderers.cpp)
target_link_libraries(sim_protocol PUBLIC sim_physics sim_render sim_sensor sim_scene)

set(default_map "${CMAKE_CURRENT_SOURCE_DIR}/maps/chaser-depth-obstacles.json")
set(asset_directory "${CMAKE_CURRENT_SOURCE_DIR}/assets")
add_executable(automa-native-sim src/server/main.cpp src/server/server.cpp)
target_link_libraries(automa-native-sim PRIVATE sim_protocol ixwebsocket Threads::Threads)
target_compile_definitions(automa-native-sim PRIVATE
  DEFAULT_CONFIG="${default_map}" DEFAULT_ASSETS="${asset_directory}")

foreach(target sim_core sim_scene sim_physics sim_render sim_sensor sim_protocol)
  target_compile_options(${target} PRIVATE -Wall -Wextra -Wpedantic)
endforeach()

enable_testing()
add_executable(native_sim_tests tests/core.cpp)
target_link_libraries(native_sim_tests PRIVATE sim_protocol)
add_test(NAME native_sim_core COMMAND native_sim_tests
  ${CMAKE_CURRENT_SOURCE_DIR}/tests/reference.json ${default_map} ${asset_directory})
add_executable(native_sim_regression tests/regression.cpp)
target_link_libraries(native_sim_regression PRIVATE sim_protocol)
add_test(NAME native_sim_regression COMMAND native_sim_regression
  ${default_map} ${CMAKE_CURRENT_SOURCE_DIR}/tests/native-baseline.json ${asset_directory})
add_executable(native_sim_scene tests/scene.cpp)
target_link_libraries(native_sim_scene PRIVATE sim_protocol)
add_test(NAME native_sim_scene COMMAND native_sim_scene ${default_map} ${asset_directory})
add_test(NAME native_sim_include_graph COMMAND ${CMAKE_COMMAND}
  -Dsource_dir=${CMAKE_CURRENT_SOURCE_DIR}/src -P ${CMAKE_CURRENT_SOURCE_DIR}/tests/include-graph.cmake)
