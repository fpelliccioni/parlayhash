#!/usr/bin/env python3
"""Build relocated consumers and check that installation preserves unrelated headers."""
import hashlib
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from check_installed_headers import check

SOURCE = Path(__file__).resolve().parents[1]


def run(*args):
    result = subprocess.run(args, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, timeout=180)
    if result.returncode:
        raise AssertionError(f"Command failed: {args!r}\n{result.stdout}")


class InstallationTests(unittest.TestCase):
    def verify(self, includedir):
        with tempfile.TemporaryDirectory(prefix="parlay-install-") as directory:
            work = Path(directory)
            prefix = work / "prefix"
            headers = prefix / includedir
            # Existing headers model ownership by another installation. If
            # ParlayHash overwrites or accidentally selects them, fail loudly.
            sentinels = {}
            for relative in ("parlay/primitives.h", "utils/epoch.h"):
                header = headers / relative
                header.parent.mkdir(parents=True, exist_ok=True)
                header.write_text('#error "Unrelated header selected by ParlayHash"\n')
                sentinels[relative] = hashlib.sha256(header.read_bytes()).digest()
            run("cmake", "-S", str(SOURCE), "-B", str(work / "build"),
                f"-DCMAKE_INSTALL_PREFIX={prefix}", f"-DCMAKE_INSTALL_INCLUDEDIR={includedir}")
            run("cmake", "--install", str(work / "build"))
            check(headers)
            self.assertFalse((headers / "parlay_hash").exists())
            self.assertFalse((headers / "old_parlay_hash").exists())
            for relative, digest in sentinels.items():
                self.assertEqual(hashlib.sha256((headers / relative).read_bytes()).digest(), digest)
            relocated = work / "relocated"
            shutil.move(str(prefix), str(relocated))
            run("cmake", "-S", str(SOURCE / "test_package"), "-B", str(work / "consumer"),
                f"-DCMAKE_PREFIX_PATH={relocated}")
            run("cmake", "--build", str(work / "consumer"), "--parallel", "2")
            executable = work / "consumer" / "consumer"
            if not executable.exists():
                executable = work / "consumer" / "consumer.exe"
            run(str(executable))

    def test_default_header_directory(self):
        self.verify("include")

    def test_custom_header_directory(self):
        self.verify("headers/custom")


if __name__ == "__main__":
    unittest.main()
