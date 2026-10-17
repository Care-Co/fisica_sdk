cmake_minimum_required(VERSION 3.16)
project(fisica_core LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
    set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type" FORCE)
endif()
# Generate the release metadata on the trusted build host before (cross-)compiling:
#   python tools/native_build.py --metadata-only
# It trusts the production entitlement keys only, as every customer core must. A QA core that
# also trusts the repository test key (its secret is committed) needs both
#   python tools/native_build.py --metadata-only --validation   and   -DFISICA_VALIDATION=ON
set(FISICA_RELEASE_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../.build/release-generated" CACHE PATH "Generated release metadata")
option(FISICA_VALIDATION "QA build: accept release metadata that trusts the test entitlement key" OFF)
set(FISICA_RELEASE_HEADER "${FISICA_RELEASE_DIR}/fisica_release.h")
if(NOT EXISTS "${FISICA_RELEASE_HEADER}")
    message(FATAL_ERROR "Generate release metadata first: python tools/native_build.py --metadata-only")
endif()
# Configure again (and repeat this check) whenever the metadata is regenerated.
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${FISICA_RELEASE_HEADER}")
file(STRINGS "${FISICA_RELEASE_HEADER}" FISICA_VALIDATION_DEFINE REGEX "^#define FISICA_VALIDATION_BUILD [01]$")
if(NOT FISICA_VALIDATION_DEFINE)
    message(FATAL_ERROR "${FISICA_RELEASE_HEADER} predates the key check; regenerate it: python tools/native_build.py --metadata-only")
endif()
if(FISICA_VALIDATION_DEFINE STREQUAL "#define FISICA_VALIDATION_BUILD 1" AND NOT FISICA_VALIDATION)
    message(FATAL_ERROR "${FISICA_RELEASE_HEADER} trusts the test entitlement key (a developer or --validation build). "
        "Customer cores trust production keys only: python tools/native_build.py --metadata-only. "
        "For a QA build pass -DFISICA_VALIDATION=ON.")
endif()
set(FISICA_SOURCES src/fisica_core.cpp src/fisica_session.cpp src/fisica_entitlement.cpp
    src/fisica_monocypher.cpp src/fisica_monocypher_ed25519.cpp)
# Customer packages use the shared core, or for Apple the pre-linked static archive of tools/build_platforms.py:
# a plain static library keeps hidden helpers (signature check, Monocypher) replaceable by whoever links it.
option(FISICA_CORE_SHARED "Build shared core instead of a static host library" ON)
if(FISICA_CORE_SHARED)
    add_library(fisica_core SHARED ${FISICA_SOURCES})
else()
    add_library(fisica_core STATIC ${FISICA_SOURCES})
    target_compile_definitions(fisica_core PUBLIC FISICA_CORE_STATIC)
endif()
target_include_directories(fisica_core PUBLIC include PRIVATE "${FISICA_RELEASE_DIR}")
target_compile_definitions(fisica_core PRIVATE FISICA_CORE_BUILD)
# The core reads the app ID (CoreFoundation) or the Windows MachineGuid (advapi32) itself.
if(APPLE)
    target_link_libraries(fisica_core PRIVATE "-framework CoreFoundation")
elseif(WIN32)
    target_link_libraries(fisica_core PRIVATE advapi32)
endif()
if(MSVC)
    target_compile_options(fisica_core PRIVATE /fp:strict)
    set_property(TARGET fisica_core PROPERTY MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
else()
    target_compile_options(fisica_core PRIVATE -ffp-contract=off)
endif()
set_target_properties(fisica_core PROPERTIES CXX_VISIBILITY_PRESET hidden VISIBILITY_INLINES_HIDDEN YES)
if(APPLE)
    set_target_properties(fisica_core PROPERTIES INSTALL_NAME_DIR "@rpath")
endif()
install(TARGETS fisica_core LIBRARY DESTINATION lib RUNTIME DESTINATION lib ARCHIVE DESTINATION lib)
install(FILES include/fisica_core.h DESTINATION include)
install(FILES "${FISICA_RELEASE_DIR}/release.json" DESTINATION .)
install(FILES "${CMAKE_CURRENT_SOURCE_DIR}/../LICENSE" DESTINATION .)
