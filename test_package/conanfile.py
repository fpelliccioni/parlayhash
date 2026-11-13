from conan import ConanFile
from conan.tools.cmake import CMake, cmake_layout
from conan.tools.build import can_run
import os
from pathlib import Path


class Consumer(ConanFile):
    settings = "os", "arch", "compiler", "build_type"
    generators = "CMakeDeps", "CMakeToolchain"
    test_type = "explicit"

    def requirements(self):
        self.requires(self.tested_reference_str)

    def layout(self):
        cmake_layout(self)

    def build(self):
        dependency = self.dependencies["parlayhash"]
        root = Path(dependency.package_folder) / "include"
        # The package owns exactly one subtree of the common include root.
        if {path.name for path in root.iterdir()} != {"parlayhash"}:
            raise RuntimeError("Package leaks headers into the shared include root")
        bundled = root / "parlayhash"
        if {path.name for path in bundled.iterdir()} != {"parlay_hash", "parlay", "utils"}:
            raise RuntimeError("Unexpected bundled header directories")
        for directory, header in (("parlay_hash", "unordered_map.h"),
                                  ("parlay", "primitives.h"), ("utils", "epoch.h")):
            if not (bundled / directory / header).is_file():
                raise RuntimeError(f"Missing required header: {directory}/{header}")
        cmake = CMake(self)
        cmake.configure()
        cmake.build()

    def test(self):
        if can_run(self):
            self.run(os.path.join(self.cpp.build.bindir, "consumer"), env="conanrun")
