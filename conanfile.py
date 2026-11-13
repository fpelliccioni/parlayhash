from conan import ConanFile
from conan.tools.build import check_min_cppstd
from conan.tools.files import copy
import os


class ParlayHashConan(ConanFile):
    name = "parlayhash"
    package_type = "header-library"
    license = "MIT"
    url = "https://github.com/fpelliccioni/parlayhash"
    description = "Concurrent hash map with parallel memory reclamation"
    settings = "os", "arch", "compiler", "build_type"
    exports_sources = "include/*", "LICENSE"

    def set_version(self):
        self.version = self.version or "1.0.0"

    def validate(self):
        check_min_cppstd(self, "23")

    def package(self):
        copy(self, "*", src=os.path.join(self.source_folder, "include"),
             dst=os.path.join(self.package_folder, "include"))
        copy(self, "LICENSE", src=self.source_folder,
             dst=os.path.join(self.package_folder, "licenses"))

    def package_id(self):
        self.info.clear()

    def package_info(self):
        self.cpp_info.set_property("cmake_file_name", "ParlayHash")
        self.cpp_info.set_property("cmake_target_name", "ParlayHash::parlay")
        self.cpp_info.bindirs = []
        self.cpp_info.libdirs = []
        if self.settings.os in ("Linux", "FreeBSD"):
            self.cpp_info.system_libs = ["pthread"]
