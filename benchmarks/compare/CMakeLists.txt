cmake_minimum_required(VERSION 3.25)
project(parlay_cfm_comparison LANGUAGES CXX)
if(POLICY CMP0135)
  cmake_policy(SET CMP0135 NEW)
endif()

get_property(COMPARE_MULTI_CONFIG GLOBAL PROPERTY GENERATOR_IS_MULTI_CONFIG)
if(NOT COMPARE_MULTI_CONFIG AND NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type" FORCE)
endif()
find_package(Threads REQUIRED)
get_filename_component(PARLAY_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/../.." ABSOLUTE)

# Pin the baseline. Updating Boost is an explicit change to the experiment.
set(BOOST_DIR "" CACHE PATH "Existing Boost headers root; empty downloads Boost 1.92.0")
if(NOT BOOST_DIR)
  include(FetchContent)
  FetchContent_Declare(boost_headers
    URL https://archives.boost.io/release/1.92.0/source/boost_1_92_0.tar.gz
    URL_HASH SHA256=c4a3b310ddd2472416e091067166b0713be97c63f38c212c484ada022fd296ce
    SOURCE_SUBDIR unused_header_only_download)
  FetchContent_MakeAvailable(boost_headers)
  set(BOOST_DIR "${boost_headers_SOURCE_DIR}")
endif()
if(NOT EXISTS "${BOOST_DIR}/boost/unordered/concurrent_flat_map.hpp")
  message(FATAL_ERROR "BOOST_DIR must contain boost/unordered/concurrent_flat_map.hpp")
endif()

option(COMPARE_JEMALLOC "Use jemalloc for both maps and measure allocated bytes" ON)
if(COMPARE_JEMALLOC)
  find_path(JEMALLOC_INCLUDE_DIR jemalloc/jemalloc.h REQUIRED)
  find_library(JEMALLOC_LIBRARY jemalloc REQUIRED)
endif()

foreach(map parlay_hash boost_hash)
  add_executable(${map} ../test_map.cpp)
  set_target_properties(${map} PROPERTIES CXX_STANDARD 23 CXX_STANDARD_REQUIRED YES CXX_EXTENSIONS NO)
  target_include_directories(${map} PRIVATE
    "${PARLAY_ROOT}/include" "${PARLAY_ROOT}/other/${map}" "${BOOST_DIR}")
  target_link_libraries(${map} PRIVATE Threads::Threads)
  if(COMPARE_JEMALLOC)
    target_compile_definitions(${map} PRIVATE JEMALLOC)
    target_include_directories(${map} PRIVATE "${JEMALLOC_INCLUDE_DIR}")
    target_link_libraries(${map} PRIVATE "${JEMALLOC_LIBRARY}")
  endif()
endforeach()

file(STRINGS "${BOOST_DIR}/boost/version.hpp" BOOST_VERSION_LINE
  REGEX "^#define BOOST_LIB_VERSION ")
# Generate configuration-specific metadata next to the corresponding executables.
if(COMPARE_MULTI_CONFIG)
  set(COMPARISON_METADATA_DIR "${CMAKE_BINARY_DIR}/$<CONFIG>")
  set(COMPARISON_CONFIG_FLAGS "")
  foreach(config IN LISTS CMAKE_CONFIGURATION_TYPES)
    string(TOUPPER "${config}" config_upper)
    string(APPEND COMPARISON_CONFIG_FLAGS
      "$<$<CONFIG:${config}>:${CMAKE_CXX_FLAGS_${config_upper}}>")
  endforeach()
else()
  set(COMPARISON_METADATA_DIR "${CMAKE_BINARY_DIR}")
  string(TOUPPER "${CMAKE_BUILD_TYPE}" config_upper)
  set(COMPARISON_CONFIG_FLAGS "${CMAKE_CXX_FLAGS_${config_upper}}")
endif()
file(GENERATE OUTPUT "${COMPARISON_METADATA_DIR}/comparison-build.txt" CONTENT
  "${BOOST_VERSION_LINE}\nBoost headers: ${BOOST_DIR}\nCompiler: ${CMAKE_CXX_COMPILER_ID} ${CMAKE_CXX_COMPILER_VERSION}\nBuild: $<CONFIG>\nFlags: ${CMAKE_CXX_FLAGS} ${COMPARISON_CONFIG_FLAGS}\nJemalloc: ${COMPARE_JEMALLOC}\n")
