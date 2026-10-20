import os
import tempfile
import unittest
from pathlib import Path

from seqtrak_sampler.files import FolderScanner, safe_filename, unique_path


class FileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_safe_filename(self):
        self.assertEqual(safe_filename("Lofi/Rhodes:01"), "Lofi_Rhodes_01")
        self.assertEqual(safe_filename("  ..  "), "sample")

    def test_unique_path(self):
        self.assertEqual(unique_path(self.tmp, "a", ".wav"), self.tmp / "a.wav")
        (self.tmp / "a.wav").touch()
        (self.tmp / "a 2.wav").touch()
        self.assertEqual(unique_path(self.tmp, "a", ".wav"), self.tmp / "a 3.wav")
        self.assertEqual(unique_path(self.tmp, "a", ".wav", keep=self.tmp / "a.wav"),
                         self.tmp / "a.wav")

    def test_scanner_ignores_existing_and_waits_for_stable_files(self):
        (self.tmp / "old.Y3S").write_bytes(b"x")
        scanner = FolderScanner(self.tmp)
        self.assertEqual(scanner.poll(), [])

        new = self.tmp / "new.Y3S"
        new.write_bytes(b"abc")
        (self.tmp / "notes.txt").write_bytes(b"ignored")
        self.assertEqual(scanner.poll(), [])        # first sighting
        self.assertEqual(scanner.poll(), [new])     # unchanged since: ready
        self.assertEqual(scanner.poll(), [])        # reported once

    def test_scanner_reports_rewritten_files(self):
        path = self.tmp / "take.y3s"
        path.write_bytes(b"one")
        scanner = FolderScanner(self.tmp)
        path.write_bytes(b"three")
        os.utime(path, ns=(1, 1))
        scanner.poll()
        self.assertEqual(scanner.poll(), [path])

    def test_scanner_survives_missing_folder(self):
        scanner = FolderScanner(self.tmp / "missing")
        self.assertEqual(scanner.poll(), [])


if __name__ == "__main__":
    unittest.main()
